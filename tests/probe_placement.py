"""Probe: does the Gegl translate/write-buffer placement actually work?

Places a known red 1024x1024 patch at (81,375) in a 2048x1536 layer, two ways,
and reports where the red actually landed.
"""
import os

import gi
gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, Gegl  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_result.txt")
PASTE = (81, 375)
SIZE = (1024, 1024)
lines = []


def red_source_image():
    img = Gimp.Image.new(SIZE[0], SIZE[1], Gimp.ImageBaseType.RGB)
    lay = Gimp.Layer.new(img, "src", SIZE[0], SIZE[1],
                         Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
    img.insert_layer(lay, None, 0)
    Gimp.context_set_foreground(Gegl.Color.new("red"))
    lay.edit_fill(Gimp.FillType.FOREGROUND)
    return img, lay


def red_bbox(layer, w, h):
    data = layer.get_buffer().get(
        Gegl.Rectangle.new(0, 0, w, h), 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.NONE)
    x0, y0, x1, y1, n = w, h, -1, -1, 0
    for i in range(0, len(data), 4):
        if data[i] > 150 and data[i + 1] < 100 and data[i + 3] > 128:
            p = i // 4
            x, y = p % w, p // w
            n += 1
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
    return n, (x0, y0, x1, y1)


try:
    # --- Method A: the current Gegl translate + write-buffer chain ---
    dest = Gimp.Image.new(2048, 1536, Gimp.ImageBaseType.RGB)
    target = Gimp.Layer.new(dest, "target", 2048, 1536,
                            Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
    dest.insert_layer(target, None, 0)
    src_img, src_layer = red_source_image()

    graph = Gegl.Node()
    node_in = graph.create_child("gegl:buffer-source")
    node_in.set_property("buffer", src_layer.get_buffer())
    tr = graph.create_child("gegl:translate")
    tr.set_property("x", float(PASTE[0]))
    tr.set_property("y", float(PASTE[1]))
    out = graph.create_child("gegl:write-buffer")
    out.set_property("buffer", target.get_shadow_buffer())
    node_in.link(tr)
    tr.link(out)
    out.process()
    target.get_shadow_buffer().flush()
    target.merge_shadow(True)
    target.update(0, 0, 2048, 1536)

    n, bbox = red_bbox(target, 2048, 1536)
    lines.append(f"METHOD A (gegl translate): red px {n}, bbox {bbox}")
    lines.append(f"  expected bbox ({PASTE[0]},{PASTE[1]},{PASTE[0]+SIZE[0]-1},{PASTE[1]+SIZE[1]-1})")
    src_img.delete()
    dest.delete()

    # --- Method B: GIMP layer copy + set_offsets ---
    dest2 = Gimp.Image.new(2048, 1536, Gimp.ImageBaseType.RGB)
    base = Gimp.Layer.new(dest2, "base", 2048, 1536,
                          Gimp.ImageType.RGBA_IMAGE, 100.0, Gimp.LayerMode.NORMAL)
    dest2.insert_layer(base, None, 0)
    src_img2, src_layer2 = red_source_image()

    copied = Gimp.Layer.new_from_drawable(src_layer2, dest2)
    dest2.insert_layer(copied, None, 0)
    copied.set_offsets(PASTE[0], PASTE[1])
    dest2.flatten()
    flat = dest2.get_layers()[0]

    n2, bbox2 = red_bbox(flat, 2048, 1536)
    lines.append(f"METHOD B (layer copy + offsets): red px {n2}, bbox {bbox2}")
    src_img2.delete()
    dest2.delete()
except Exception:
    import traceback
    lines.append("ERROR")
    lines.append(traceback.format_exc())

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines))
Gimp.quit()
