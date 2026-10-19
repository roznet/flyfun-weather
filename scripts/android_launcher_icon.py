#!/usr/bin/env python3
"""Regenerate the Android launcher icon from the iOS app icon.

Same approach as flyfun-forms' ``scripts/android_launcher_icon.py``. The iOS
icon (``AppIcon.appiconset/AppLogo.png``) is full-bleed artwork. Android wants
an adaptive icon instead: layers on a 108 dp canvas of which only the middle
72 dp shows, cut to whatever shape the launcher uses. This writes, for each
density:

- ``ic_launcher_background.png``: the iOS artwork scaled to the 66 dp safe
  zone, its edges stretched out to fill the rest of the canvas, which the
  launcher uses for parallax and shape cropping. Scaling the artwork to the
  full 108 dp instead would crop the red altitude bar on the right.
- no foreground: ``mipmap-anydpi/ic_launcher.xml`` uses a transparent colour.
  The artwork is one picture; split into layers it would drift apart as the
  launcher animates them.
- ``ic_launcher_monochrome.png``: everything that is not sky (cloud, the
  dashed levels, the red bar, the terrain) as an alpha mask, for Android 13+
  themed icons, which tint it with the wallpaper's colours.

No legacy bitmap ``ic_launcher.png``: minSdk is 31, so every launcher uses the
adaptive icon.

Run it when the iOS icon changes, and commit the result:

    python scripts/android_launcher_icon.py
"""

from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (
    ROOT / "app" / "flyfun-weather" / "flyfun-weather" / "Assets.xcassets"
    / "AppIcon.appiconset" / "AppLogo.png"
)
RES = ROOT / "app" / "android" / "app" / "src" / "main" / "res"

# Adaptive icon canvas in px at each density: 108 dp.
DENSITIES = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

# Where the artwork sits: the 66 dp safe zone of the 108 dp canvas, which no
# launcher mask crops. At the full visible 72 dp a circle mask clips the ends
# of the red bar.
VISIBLE = 66 / 108

# Source px trimmed from each edge of the 1024 px artwork before stretching.
EDGE_TRIM = 8


def full_canvas(art: np.ndarray) -> np.ndarray:
    """The artwork centred on the 108 dp canvas, its edge pixels stretched outwards."""
    # The terrain has a dark outline along the artwork's edges; stretched, it
    # would become a dark band, so stretch from just inside it.
    art = art[EDGE_TRIM:-EDGE_TRIM, EDGE_TRIM:-EDGE_TRIM]
    size = art.shape[0]
    margin = round(size * (1 / VISIBLE - 1) / 2)
    pad = ((margin, margin), (margin, margin), (0, 0))
    return np.pad(art, pad, mode="edge")


def not_sky_mask(rgb: np.ndarray) -> np.ndarray:
    """Alpha for everything that is not sky, soft at the edges."""
    rgb = rgb.astype(np.float32)
    # The sky is the only strongly blue part (blue - red >= 90); the cloud is
    # ~10, the dark dashes ~35, the red bar and terrain negative.
    blueness = rgb[..., 2] - rgb[..., 0]
    return (np.clip((70 - blueness) / 30, 0, 1) * 255).astype(np.uint8)


def main() -> None:
    canvas = full_canvas(np.asarray(Image.open(SOURCE).convert("RGB")))
    mask = not_sky_mask(canvas)
    background = Image.fromarray(canvas, "RGB")
    monochrome = Image.fromarray(
        np.dstack([np.full_like(mask, 255)] * 3 + [mask]), "RGBA"
    )

    for density, px in DENSITIES.items():
        out = RES / f"mipmap-{density}"
        out.mkdir(parents=True, exist_ok=True)
        background.resize((px, px), Image.LANCZOS).save(out / "ic_launcher_background.png", optimize=True)
        monochrome.resize((px, px), Image.LANCZOS).save(out / "ic_launcher_monochrome.png", optimize=True)
        print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
