"""Generate ASCII art from assets/photo.webp, ignoring the background.

Usage: python scripts/ascii_art.py [rows]
Writes assets/ascii_dark.txt (dense = bright) and assets/ascii_light.txt (dense = dark).
Run once locally (needs Pillow + numpy); the daily Action only reads the .txt files.
"""
import colorsys
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 32
CELL_ASPECT = 2.05  # monospace cell height / width at the SVG font size
CROP = tuple(int(v) for v in os.environ.get("CROP", "450,0,1650,1650").split(","))  # left, top, right, bottom in the 2000x2000 photo
RAMP = os.environ.get("RAMP", " .,:;i1tfLCG08@")


def foreground_mask(rgba: np.ndarray) -> np.ndarray:
    alpha = rgba[..., 3] > 128
    rgb = rgba[..., :3].astype(float) / 255
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    # blue -> purple gradient circle: saturated and blue-dominant
    backdrop = (sat > 0.45) & (b > r + 0.15) & (b > g + 0.1)
    return alpha & ~backdrop


def main() -> None:
    img = Image.open(ROOT / "assets/photo.webp").convert("RGBA").crop(CROP)
    w, h = img.size
    cell_h = h / ROWS
    cols = round(w / (cell_h / CELL_ASPECT))
    arr = np.asarray(img)
    mask = foreground_mask(arr)
    # sharpen so eyes, brows and beard edges survive the downsampling
    sharp = img.convert("L").filter(ImageFilter.UnsharpMask(radius=int(os.environ.get("SHARP_R", 18)), percent=int(os.environ.get("SHARP_P", 180)), threshold=2))
    lum = np.asarray(sharp, dtype=float) / 255

    cells_lum = np.zeros((ROWS, cols))
    cells_fg = np.zeros((ROWS, cols), bool)
    for y in range(ROWS):
        for x in range(cols):
            y0, y1 = int(y * h / ROWS), int((y + 1) * h / ROWS)
            x0, x1 = int(x * w / cols), int((x + 1) * w / cols)
            m = mask[y0:y1, x0:x1]
            if m.mean() >= 0.5:
                cells_fg[y, x] = True
                cells_lum[y, x] = lum[y0:y1, x0:x1][m].mean()

    # histogram-equalise the foreground so the face uses the whole ramp
    vals = cells_lum[cells_fg]
    eq = np.zeros_like(cells_lum)
    if os.environ.get("EQ") == "1":
        s = np.sort(vals)
        eq[cells_fg] = np.searchsorted(s, vals, side="right") / len(s)
    else:  # linear stretch between percentiles, keeps the beard/skin contrast
        lo, hi = np.percentile(vals, [3, 97])
        eq[cells_fg] = np.clip((vals - lo) / (hi - lo), 0, 1)

    def render(dense_is_bright: bool) -> str:
        lines = []
        for y in range(ROWS):
            row = ""
            for x in range(cols):
                if not cells_fg[y, x]:
                    row += " "
                    continue
                v = eq[y, x] if dense_is_bright else 1 - eq[y, x]
                row += RAMP[1 + int(v * (len(RAMP) - 2))]
            lines.append(row.rstrip())
        return "\n".join(lines) + "\n"

    (ROOT / "assets/ascii_dark.txt").write_text(render(True))
    (ROOT / "assets/ascii_light.txt").write_text(render(False))
    print(f"{cols}x{ROWS}")


if __name__ == "__main__":
    main()
