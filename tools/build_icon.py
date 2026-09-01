"""
build_icon.py
-------------
Generate ``assets/app.ico`` for the packaged Windows build.

The square ScaleOn mark is used (the wide wordmark would be squashed at icon
sizes). Run this only when the branding changes:

    python tools/build_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# This script lives in tools/, so the project root is one level up.
BASE = Path(__file__).resolve().parent.parent
SOURCES = (BASE / "assets" / "watermark" / "watermark.png",
           BASE / "assets" / "logo" / "logo.png")
TARGET = BASE / "assets" / "app.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
         (256, 256)]


def main() -> int:
    source = next((p for p in SOURCES if p.exists()), None)
    if source is None:
        print("No branding image found; skipping icon.")
        return 1

    image = Image.open(source).convert("RGBA")

    # Trim fully transparent margins so the mark fills the icon.
    box = image.getbbox()
    if box:
        image = image.crop(box)

    # Pad to a square on a transparent canvas - never distort the aspect ratio.
    side = max(image.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))

    canvas.save(TARGET, format="ICO", sizes=SIZES)
    print(f"wrote {TARGET.relative_to(BASE)} from "
          f"{source.relative_to(BASE)} ({canvas.size[0]}px square)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
