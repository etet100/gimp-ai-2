#!/usr/bin/env python3
"""
GIMP-side pipeline tests: mask construction and layer flattening.

Unlike the other tests here, this one needs a real GIMP because the mask is
built with GIMP image operations. Run it headless from the repository root:

    gimp -i --batch-interpreter=python-fu-eval \\
         -b "import runpy; runpy.run_path('$PWD/tests/test_gimp_pipeline.py')"

(runpy is used rather than exec(open(...)) so that __file__ is defined, which
is how the test locates the repository.)

Results are written to tests/mask_test_result.txt (and two PNGs beside it) so
they survive GIMP exiting.

Covered:
  - the selection actually lands in the mask, in the right place, with the
    right polarity for each provider - easy to get wrong and invisible until
    an inpaint silently does nothing
  - layer flattening for Bedrock composites produces one correctly sized image
    with the upper layer present in it

GimpAIPlugin cannot be instantiated outside the plug-in protocol, so its
methods are rebound onto a plain object carrying only the state they use.
"""
import importlib.util
import os
import sys

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gegl  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE) if os.path.basename(HERE) == "tests" else HERE
OUT_DIR = os.path.join(REPO, "tests")
OUT = os.path.join(OUT_DIR, "mask_test_result.txt")

# A selection deliberately far from the top-left corner: a mask built at the
# image origin instead of the extract region looks fine for a corner selection
# and is completely empty for this one.
IMAGE_SIZE = (2048, 1536)
SELECTION = (513, 808, 160, 159)

lines = []
failures = []


def check(label, condition, detail=""):
    lines.append(f"{'PASS' if condition else 'FAIL'}: {label} {detail}".rstrip())
    if not condition:
        failures.append(label)


def alpha_bbox(png_path, transparent=True):
    """Bounding box and count of transparent (or white) pixels, without PIL."""
    image = Gimp.file_load(
        run_mode=Gimp.RunMode.NONINTERACTIVE,
        file=Gio.File.new_for_path(png_path),
    )
    layer = image.get_layers()[0]
    width, height = layer.get_width(), layer.get_height()
    buffer = layer.get_buffer()
    rect = Gegl.Rectangle.new(0, 0, width, height)
    data = buffer.get(rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE)

    count = 0
    x0, y0, x1, y1 = width, height, -1, -1
    for index in range(0, len(data), 4):
        r, _g, _b, a = data[index], data[index + 1], data[index + 2], data[index + 3]
        hit = (a < 50) if transparent else (a >= 50 and r > 200)
        if hit:
            pixel = index // 4
            x, y = pixel % width, pixel // width
            count += 1
            x0, y0 = min(x0, x), min(y0, y)
            x1, y1 = max(x1, x), max(y1, y)
    image.delete()
    return count, (x0, y0, x1, y1)


try:
    from gi.repository import Gio

    sys.path.insert(0, REPO)
    spec = importlib.util.spec_from_file_location(
        "plugin_under_test", os.path.join(REPO, "gimp-ai-plugin.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    methods = {k: v for k, v in vars(mod.GimpAIPlugin).items() if callable(v)}
    plugin = type("Stub", (object,), methods).__new__(type("Stub", (object,), methods))
    plugin.config = {"debug_mode": False}
    plugin._cancel_requested = False

    image = Gimp.Image.new(*IMAGE_SIZE, Gimp.ImageBaseType.RGB)
    layer = Gimp.Layer.new(
        image, "bg", IMAGE_SIZE[0], IMAGE_SIZE[1],
        Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL,
    )
    image.insert_layer(layer, None, 0)
    Gimp.context_set_foreground(Gegl.Color.new("gray"))
    layer.edit_fill(Gimp.FillType.FOREGROUND)
    image.select_rectangle(Gimp.ChannelOps.REPLACE, *SELECTION)

    context_info = plugin._calculate_context_extraction(image)
    extract = context_info["extract_region"]
    target_w, target_h = context_info["target_shape"]
    lines.append(f"extract_region: {extract}, target: {target_w}x{target_h}")

    # Where the selection should end up after cropping, scaling and padding
    scale = context_info["padding_info"]["scale_factor"]
    pad_left, pad_top = context_info["padding_info"]["padding"][:2]
    exp_x0 = round((SELECTION[0] - extract[0]) * scale) + pad_left
    exp_y0 = round((SELECTION[1] - extract[1]) * scale) + pad_top
    exp_x1 = round((SELECTION[0] + SELECTION[2] - extract[0]) * scale) + pad_left
    exp_y1 = round((SELECTION[1] + SELECTION[3] - extract[1]) * scale) + pad_top
    lines.append(f"expected mask region: ({exp_x0},{exp_y0})-({exp_x1},{exp_y1})")

    raw = plugin._create_context_mask(image, context_info, context_info["target_size"])
    raw_path = os.path.join(OUT_DIR, "mask_test_raw.png")
    with open(raw_path, "wb") as f:
        f.write(raw)

    converted = plugin._convert_mask_to_bedrock(raw)
    conv_path = os.path.join(OUT_DIR, "mask_test_converted.png")
    with open(conv_path, "wb") as f:
        f.write(converted)

    # OpenAI convention: the selection is transparent, everything else opaque
    n_transparent, bbox = alpha_bbox(raw_path, transparent=True)
    check("openai mask has a transparent region", n_transparent > 0, f"({n_transparent} px)")
    check(
        "openai transparent region matches the selection",
        n_transparent and abs(bbox[0] - exp_x0) <= 3 and abs(bbox[1] - exp_y0) <= 3
        and abs(bbox[2] - exp_x1) <= 3 and abs(bbox[3] - exp_y1) <= 3,
        f"got {bbox}",
    )

    # Bedrock convention: the selection is white, everything else black
    n_white, wbbox = alpha_bbox(conv_path, transparent=False)
    check("bedrock mask has a white region", n_white > 0, f"({n_white} px)")
    check(
        "bedrock white region matches the selection",
        n_white and abs(wbbox[0] - exp_x0) <= 3 and abs(wbbox[1] - exp_y0) <= 3
        and abs(wbbox[2] - exp_x1) <= 3 and abs(wbbox[3] - exp_y1) <= 3,
        f"got {wbbox}",
    )
    check(
        "bedrock mask does not inpaint the whole canvas",
        n_white < (target_w * target_h) // 2,
        f"({n_white} of {target_w * target_h})",
    )

    # --- Layer flattening for Bedrock composites ---
    from coordinate_utils import (
        calculate_padding_for_shape,
        get_optimal_openai_shape,
    )

    # A second layer with an obvious marker, so we can prove it was composited
    top = Gimp.Layer.new(
        image, "top", IMAGE_SIZE[0], IMAGE_SIZE[1],
        Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL,
    )
    image.insert_layer(top, None, 0)
    Gimp.Selection.none(image)
    image.select_rectangle(Gimp.ChannelOps.REPLACE, 900, 600, 300, 300)
    Gimp.context_set_foreground(Gegl.Color.new("red"))
    top.edit_fill(Gimp.FillType.FOREGROUND)
    Gimp.Selection.none(image)

    shape = get_optimal_openai_shape(*IMAGE_SIZE)
    composite_context = {
        "mode": "full",
        "selection_bounds": (0, 0, IMAGE_SIZE[0], IMAGE_SIZE[1]),
        "extract_region": (0, 0, IMAGE_SIZE[0], IMAGE_SIZE[1]),
        "target_shape": shape,
        "target_size": max(shape),
        "needs_padding": True,
        "padding_info": calculate_padding_for_shape(
            IMAGE_SIZE[0], IMAGE_SIZE[1], shape[0], shape[1]
        ),
        "has_selection": False,
    }

    ok, flat_message, flat_b64 = plugin._flatten_layers_for_bedrock(
        image, [layer, top], composite_context
    )
    check("layer flattening succeeds", ok, flat_message)

    if ok:
        import base64
        flat_path = os.path.join(OUT_DIR, "mask_test_flattened.png")
        with open(flat_path, "wb") as f:
            f.write(base64.b64decode(flat_b64))
        flat = Gimp.file_load(
            run_mode=Gimp.RunMode.NONINTERACTIVE,
            file=Gio.File.new_for_path(flat_path),
        )
        flat_layer = flat.get_layers()[0]
        check(
            "flattened image is a single layer at the target shape",
            len(flat.get_layers()) == 1
            and (flat_layer.get_width(), flat_layer.get_height()) == shape,
            f"got {len(flat.get_layers())} layer(s) "
            f"{flat_layer.get_width()}x{flat_layer.get_height()}, want {shape}",
        )

        # The red marker sits at (900,600)-(1200,900) in image space; check the
        # same spot after scaling and padding.
        pad_info = composite_context["padding_info"]
        sx = round(1050 * pad_info["scale_factor"]) + pad_info["padding"][0]
        sy = round(750 * pad_info["scale_factor"]) + pad_info["padding"][1]
        sample = flat_layer.get_buffer().get(
            Gegl.Rectangle.new(sx, sy, 1, 1), 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE
        )
        check(
            "upper layer is present in the flattened image",
            sample[0] > 150 and sample[1] < 100 and sample[2] < 100,
            f"pixel at ({sx},{sy}) = {tuple(sample[:4])}, want reddish",
        )
        flat.delete()

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
