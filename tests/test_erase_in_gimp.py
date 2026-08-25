#!/usr/bin/env python3
"""
Erase Object pipeline test that runs inside GIMP against a real photo.

    gimp -i --batch-interpreter=python-fu-eval \
         -b "import runpy; runpy.run_path('$PWD/tests/test_erase_in_gimp.py')"

Set ERASE_TEST_PHOTO to a JPEG/PNG and ERASE_TEST_SELECTION to "x1,y1,x2,y2";
without them the test is skipped, since it needs a photo with something in it
worth erasing. Makes one live Bedrock call, so it costs money and needs
credentials in the environment.

Writes tests/erase_test_result.txt and tests/erase_test_output.png.
"""
import importlib.util
import os
import sys

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gio  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "erase_test_result.txt")

lines = []
failures = []


def check(label, condition, detail=""):
    lines.append(f"{'PASS' if condition else 'FAIL'}: {label} {detail}".rstrip())
    if not condition:
        failures.append(label)


try:
    photo = os.environ.get("ERASE_TEST_PHOTO")
    sel_env = os.environ.get("ERASE_TEST_SELECTION")
    if not photo or not sel_env or not os.path.exists(photo):
        lines.append("SKIPPED: set ERASE_TEST_PHOTO and ERASE_TEST_SELECTION")
    else:
        sel = [int(v) for v in sel_env.split(",")]
        sys.path.insert(0, REPO)
        spec = importlib.util.spec_from_file_location(
            "plugin_under_test", os.path.join(REPO, "gimp-ai-plugin.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        methods = {k: v for k, v in vars(mod.GimpAIPlugin).items() if callable(v)}
        Stub = type("Stub", (object,), methods)
        plugin = Stub.__new__(Stub)
        plugin.config = {"debug_mode": False, "api_provider": "bedrock", "bedrock": {}}
        plugin._cancel_requested = False

        image = Gimp.file_load(
            run_mode=Gimp.RunMode.NONINTERACTIVE, file=Gio.File.new_for_path(photo)
        )
        lines.append(f"photo: {image.get_width()}x{image.get_height()}, selection {sel}")
        image.select_rectangle(
            Gimp.ChannelOps.REPLACE, sel[0], sel[1], sel[2] - sel[0], sel[3] - sel[1]
        )

        context_info = plugin._calculate_native_context_extraction(image)
        _, _, cw, ch = context_info["extract_region"]
        scale = context_info["padding_info"]["scale_factor"]
        lines.append(f"context: {cw}x{ch}, scale {scale}")
        check("context window is never upscaled", scale <= 1.0, f"scale {scale}")
        check("context window is much larger than the selection",
              cw >= (sel[2] - sel[0]) * 3, f"{cw} vs selection {sel[2] - sel[0]}")

        ok, message, image_data = plugin._extract_context_region(image, context_info)
        check("context extraction succeeds", ok, message)

        mask = plugin._convert_mask_to_bedrock(
            plugin._create_context_mask(image, context_info, context_info["target_size"])
        )
        check("mask produced", bool(mask), f"{len(mask) if mask else 0} bytes")

        # Dump exactly what would be sent, for inspection
        import base64 as _b64
        with open(os.path.join(HERE, "erase_test_mask.png"), "wb") as f:
            f.write(mask)
        with open(os.path.join(HERE, "erase_test_input.png"), "wb") as f:
            f.write(_b64.b64decode(image_data))
        lines.append("dumped erase_test_mask.png and erase_test_input.png")

        if os.environ.get("ERASE_TEST_SKIP_API"):
            lines.append("SKIPPED API call (ERASE_TEST_SKIP_API set)")
            image.delete()
            raise SystemExit

        lines.append(f"selection after mask build: {Gimp.Selection.bounds(image)}")

        api_ok, api_message, response = plugin._call_bedrock_erase(image_data, mask)
        check("bedrock erase call succeeds", api_ok, api_message)

        if api_ok:
            import_ok, import_message = plugin._download_and_composite_result(
                image, response, context_info, "full"
            )
            check("result composites back into the image", import_ok, import_message)
            check("a new layer was added", len(image.get_layers()) >= 2,
                  f"{len(image.get_layers())} layers")

            top = image.get_layers()[0]
            lines.append(f"top layer: {top.get_name()} {top.get_width()}x{top.get_height()}"
                         f" offsets {top.get_offsets()[1:]}")
            layer_mask = top.get_mask()
            lines.append(f"layer has mask: {layer_mask is not None}")
            check("no selection mask applied (window pasted whole)", layer_mask is None)
            if False:
                from gi.repository import Gegl as _Gegl
                mw, mh = layer_mask.get_width(), layer_mask.get_height()
                data = layer_mask.get_buffer().get(
                    _Gegl.Rectangle.new(0, 0, mw, mh), 1.0, "Y u8", _Gegl.AbyssPolicy.NONE)
                bright = sum(1 for v in data if v > 128)
                lines.append(f"layer mask {mw}x{mh}, bright px {bright}")
                check("layer mask is not empty", bright > 0, f"{bright} bright px")

            flat = image.duplicate()
            flat.flatten()
            out_path = os.path.join(HERE, "erase_test_output.png")
            proc = Gimp.get_pdb().lookup_procedure("file-png-export")
            cfg = proc.create_config()
            cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
            cfg.set_property("image", flat)
            cfg.set_property("file", Gio.File.new_for_path(out_path))
            cfg.set_property("options", None)
            proc.run(cfg)
            flat.delete()
            lines.append(f"wrote {out_path}")

        # --- Drive the real procedure, not just its internals ---
        # The dialog wiring in run_erase_object is not reachable by calling the
        # helpers directly, and that is precisely where a widget/tuple mismatch
        # slipped through the first time.
        class FakeProcedure:
            def new_return_values(self, status, error):
                return [status]

        image2 = Gimp.file_load(
            run_mode=Gimp.RunMode.NONINTERACTIVE, file=Gio.File.new_for_path(photo)
        )
        image2.select_rectangle(
            Gimp.ChannelOps.REPLACE, sel[0], sel[1], sel[2] - sel[0], sel[3] - sel[1]
        )
        before_layers = len(image2.get_layers())
        try:
            result = plugin.run_erase_object(
                FakeProcedure(), Gimp.RunMode.NONINTERACTIVE, image2, [], None, None
            )
            ran = True
            detail = f"returned {result}"
        except Exception as exc:
            ran = False
            detail = repr(exc)
        check("run_erase_object completes without raising", ran, detail)
        layers_after = image2.get_layers()
        check("run_erase_object added three variation layers",
              ran and len(layers_after) == before_layers + 3,
              f"{before_layers} -> {len(layers_after)}")
        if len(layers_after) >= 3:
            names = [layer.get_name() for layer in layers_after[:3]]
            vis = [layer.get_visible() for layer in layers_after[:3]]
            lines.append(f"variation layers: {names}, visible {vis}")
            check("variations are named in order",
                  names == ["AI Erase 1 of 3", "AI Erase 2 of 3", "AI Erase 3 of 3"],
                  f"got {names}")
            check("only the top variation is visible", vis == [True, False, False],
                  f"got {vis}")

            # The variations must actually differ, or the seeds did nothing
            from gi.repository import Gegl as _G
            sig = []
            for layer in layers_after[:3]:
                data = layer.get_buffer().get(
                    _G.Rectangle.new(sel[0], sel[1], sel[2] - sel[0], sel[3] - sel[1]),
                    1.0, "R'G'B'A u8", _G.AbyssPolicy.NONE)
                sig.append(sum(data[::97]))
            check("variations differ from each other", len(set(sig)) == 3, f"sums {sig}")
        image2.delete()

        image.delete()
except SystemExit:
    pass
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
