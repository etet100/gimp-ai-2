#!/usr/bin/env python3
"""
Tests for calculate_native_context() - the context window used by Bedrock.

The property that matters is "never upscale": the old OpenAI-shaped path blew a
288x287 crop up to 1024x1024, so 92% of what the model saw was interpolated and
it generated content to match. These tests pin the behaviour that replaced it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coordinate_utils import (  # noqa: E402
    MAX_ASPECT,
    MIN_ASPECT,
    NATIVE_TARGET_PIXELS,
    calculate_native_context,
)


def run_all_tests():
    failures = []

    def check(label, condition, detail=""):
        if condition:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label} {detail}")
            failures.append(label)

    print("Testing native context windows...")

    # The real photobomber case: small selection in a large photo
    r = calculate_native_context(2048, 1536, (513, 808, 673, 967))
    x, y, w, h = r["extract_region"]
    check("small selection gets a ~1MP native window", (w, h) == (1024, 1024), f"got {w}x{h}")
    check("no scaling applied", r["padding_info"]["scale_factor"] == 1.0)
    check("no padding", r["padding_info"]["padding"] == (0, 0, 0, 0))
    check("window contains the selection",
          x <= 513 and y <= 808 and x + w >= 673 and y + h >= 967,
          f"window {(x, y, w, h)}")

    # Never upscale, whatever the selection size
    for sel in [(0, 0, 10, 10), (500, 500, 520, 530), (100, 100, 1900, 1400)]:
        r = calculate_native_context(2048, 1536, sel)
        check(f"scale <= 1.0 for selection {sel}",
              r["padding_info"]["scale_factor"] <= 1.0,
              f"got {r['padding_info']['scale_factor']}")

    # A selection larger than the target still gets context, and is scaled down
    r = calculate_native_context(4000, 3000, (500, 500, 2500, 2400))
    x, y, w, h = r["extract_region"]
    tw, th = r["target_shape"]
    check("large selection window includes margin", w > 2000 and h > 1900, f"got {w}x{h}")
    check("oversized window is scaled down", r["padding_info"]["scale_factor"] < 1.0)
    check("scaled result is about the target area",
          tw * th <= NATIVE_TARGET_PIXELS * 1.05, f"got {tw * th}")

    # Window must stay inside the image even for edge and corner selections
    for sel in [(0, 0, 50, 50), (1990, 1480, 2048, 1536), (2000, 10, 2048, 60)]:
        r = calculate_native_context(2048, 1536, sel)
        x, y, w, h = r["extract_region"]
        check(f"window stays in bounds for {sel}",
              x >= 0 and y >= 0 and x + w <= 2048 and y + h <= 1536,
              f"got {(x, y, w, h)}")

    # Small images must not be upscaled to reach the target
    r = calculate_native_context(400, 300, (100, 100, 200, 200))
    x, y, w, h = r["extract_region"]
    check("small image window is clamped to the image", w <= 400 and h <= 300, f"got {w}x{h}")
    check("small image is not upscaled", r["padding_info"]["scale_factor"] <= 1.0)

    # Aspect ratio must stay within what Stability accepts
    for img, sel in [((3000, 400), (1400, 150, 1600, 250)), ((400, 3000), (150, 1400, 250, 1600))]:
        r = calculate_native_context(img[0], img[1], sel)
        _, _, w, h = r["extract_region"]
        check(f"aspect within limits for image {img}",
              MIN_ASPECT - 0.01 <= w / h <= MAX_ASPECT + 0.01, f"got {w / h:.2f}")

    if failures:
        print(f"\n{len(failures)} native context test(s) failed: {failures}")
        return False
    print("✓ All native context tests passed")
    return True


if __name__ == "__main__":
    sys.exit(0 if run_all_tests() else 1)
