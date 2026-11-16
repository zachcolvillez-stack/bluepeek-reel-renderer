"""Fit a plate's screen from its flat-white panel and print the plates.json entry.

    python3 scripts/macbook/fit_plate.py new-room.jpg "Description"

Finds the brightest near-white region, traces its top and bottom edge in every
column, and records the four corners. When the edges bow (a curved monitor)
it adds top_mid / bottom_mid, which composite.py uses to bend the site.
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def fit(path, threshold=232):
    img = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    white = (img.min(axis=2) >= threshold) & (np.ptp(img, axis=2) < 18)
    cols = np.where(white.sum(axis=0) > white.shape[0] * 0.05)[0]
    if not len(cols):
        raise SystemExit("No flat white screen found: is the screen pure white?")
    # The longest run of screen columns (ignores stray bright pixels elsewhere).
    runs = np.split(cols, np.where(np.diff(cols) > 3)[0] + 1)
    cols = max(runs, key=len)
    x0, x1 = int(cols[0]), int(cols[-1])

    def edges(x):
        # The longest white run in this column: the screen, not a lamp or a mug.
        ys = np.where(white[:, x])[0]
        runs = np.split(ys, np.where(np.diff(ys) > 2)[0] + 1)
        run = max(runs, key=len)
        return float(run[0]), float(run[-1] + 1)

    # Trace both edges across the middle 80% (rounded corners and the
    # anti-aliased sides would bend the fit) and extend the fit to the sides.
    width = x1 + 1 - x0
    xs = np.arange(x0 + int(width * 0.1), x0 + int(width * 0.9), max(1, width // 120))
    tops, bots = np.array([edges(int(x)) for x in xs]).T
    ft, fb = np.polyfit(xs, tops, 2), np.polyfit(xs, bots, 2)
    lin_t, lin_b = np.polyfit(xs, tops, 1), np.polyfit(xs, bots, 1)
    L, R, M = float(x0), float(x1 + 1), (x0 + x1 + 1) / 2
    height = float(np.polyval(lin_b, M) - np.polyval(lin_t, M))
    curved = max(np.max(np.abs(np.polyval(ft, xs) - np.polyval(lin_t, xs))),
                 np.max(np.abs(np.polyval(fb, xs) - np.polyval(lin_b, xs)))) > height * 0.006
    top, bot = (ft, fb) if curved else (lin_t, lin_b)
    y = lambda f, x: round(float(np.polyval(f, x)), 1)
    screen = {"tl": [L, y(top, L)], "tr": [R, y(top, R)], "br": [R, y(bot, R)], "bl": [L, y(bot, L)]}
    if curved:
        screen["top_mid"] = [M, y(top, M)]
        screen["bottom_mid"] = [M, y(bot, M)]
        gx = np.linspace(L, R, 65)
        width = float(np.sum(np.hypot(np.diff(gx), np.diff(np.polyval(top, gx)))))
        # The middle of a curved panel is further away and looks shorter than
        # the sides: average the two so the site isn't squashed or stretched.
        side = float((np.polyval(bot, L) - np.polyval(top, L) + np.polyval(bot, R) - np.polyval(top, R)) / 2)
        height = (height + side) / 2
    return screen, round(width / height, 4)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    screen, aspect = fit(path)
    size = list(Image.open(path).size)
    print(json.dumps({"id": path.stem, "file": path.name, "description": sys.argv[2] if len(sys.argv) > 2 else "",
                      "size": size, "screen": screen, "screen_aspect": aspect}, indent=1))
