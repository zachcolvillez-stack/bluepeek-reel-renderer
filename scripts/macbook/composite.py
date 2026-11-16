"""Put a captured desktop scroll onto a MacBook in a moody, real-looking room.

The plates in this folder are photographs (Higgsfield) of a MacBook whose
screen shows flat white; plates.json holds each screen's four corners. Every
frame is warped onto that screen and blended so it reads as a real display:

- a display's black level and a faint glass reflection, not a pasted image;
- a soft bloom from the screen onto the bezel;
- the light the screen throws on the keyboard and desk follows the content
  (a dark site dims it, a colourful one tints it);
- a slow camera push-in over the Reel;
- the room's coloured light (lamps, LED strips) re-lit in the site's brand
  colour, so a red site gets red RGB light and a gold one keeps its amber.

Grain is added at encode time by the caller.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
CONFIG = json.loads((HERE / "plates.json").read_text())
PLATES = {p["id"]: p for p in CONFIG["plates"]}


def choose_plate(seed, requested=None):
    """A requested plate, else a stable pick per job so clients vary."""
    if requested in PLATES:
        return PLATES[requested]
    ids = sorted(PLATES)
    return PLATES[ids[int(hashlib.md5(str(seed).encode()).hexdigest(), 16) % len(ids)]]


def _perspective_coeffs(dst, src):
    """PIL wants output → input coefficients: map dst quad points onto src."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    return np.linalg.solve(np.array(rows, float), np.array(rhs, float)).tolist()


def _bezier(a, mid, b, n):
    """n points along the quadratic curve from a to b that passes through mid at its halfway point."""
    a, mid, b = (np.array(v, float) for v in (a, mid, b))
    c = 2 * mid - (a + b) / 2
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 2 * a + 2 * (1 - t) * t * c + t ** 2 * b


def curved_mesh(top, bottom, src_size, region_size, cell=10):
    """
    PIL MESH data for a curved screen. top/bottom: matching points along the
    screen's top and bottom edges (left to right), in region coordinates.
    The screen is cut into thin vertical strips, each a flat perspective quad,
    so the site bends with the glass. Returns (mesh, polygon).
    """
    sw, sh = src_size
    rw, rh = region_size
    n = len(top) - 1
    strips = []
    for k in range(n):
        u0, u1 = sw * k / n, sw * (k + 1) / n
        strips.append((u0, u1, np.array(_perspective_coeffs(
            [top[k], top[k + 1], bottom[k + 1], bottom[k]], [(u0, 0), (u1, 0), (u1, sh), (u0, sh)]))))
    # Source coordinates for every mesh corner: try each strip's mapping and
    # keep the one whose u lands inside its own slice (or nearest to it).
    gx, gy = np.meshgrid(np.arange(0, rw + cell, cell, dtype=float), np.arange(0, rh + cell, cell, dtype=float))
    best_u, best_v, best_miss = np.zeros_like(gx), np.zeros_like(gx), np.full(gx.shape, np.inf)
    for u0, u1, c in strips:
        d = c[6] * gx + c[7] * gy + 1
        u = (c[0] * gx + c[1] * gy + c[2]) / d
        v = (c[3] * gx + c[4] * gy + c[5]) / d
        miss = np.maximum(u0 - u, 0) + np.maximum(u - u1, 0)
        take = miss < best_miss
        best_u[take], best_v[take], best_miss[take] = u[take], v[take], miss[take]
    mesh = []
    for j in range(gx.shape[0] - 1):
        for i in range(gx.shape[1] - 1):
            box = (int(gx[j, i]), int(gy[j, i]), int(gx[j, i + 1]), int(gy[j + 1, i]))
            mesh.append((box, (best_u[j, i], best_v[j, i], best_u[j + 1, i], best_v[j + 1, i],
                               best_u[j + 1, i + 1], best_v[j + 1, i + 1], best_u[j, i + 1], best_v[j, i + 1])))
    polygon = [tuple(p) for p in top] + [tuple(p) for p in bottom[::-1]]
    return mesh, polygon


def _rgb_to_hsv(a):
    """a: float array (..., 3) in 0..1 → h (0..1), s, v."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    v = a.max(-1); mn = a.min(-1); d = v - mn
    s = np.where(v > 0, d / np.maximum(v, 1e-6), 0)
    h = np.zeros_like(v)
    nz = d > 1e-6
    rc = np.where(nz, (v - r) / np.maximum(d, 1e-6), 0)
    gc = np.where(nz, (v - g) / np.maximum(d, 1e-6), 0)
    bc = np.where(nz, (v - b) / np.maximum(d, 1e-6), 0)
    h = np.where(v == r, bc - gc, np.where(v == g, 2 + rc - bc, 4 + gc - rc))
    h = (h / 6) % 1
    return np.where(nz, h, 0), s, v


def _hsv_to_rgb(h, s, v):
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    choices = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    out = np.zeros(h.shape + (3,), np.float32)
    for k, (r, g, b) in enumerate(choices):
        m = i == k
        out[m] = np.stack([r[m], g[m], b[m]], -1)
    return out


def relight(img, hex_colour):
    """Shift the room's warm practical light (amber lamps, LED strips and their
    glow on walls and desks) to the brand colour. Neutral surfaces — the
    laptop, desk grain in shadow, the night window — are left alone because
    only saturated warm pixels move. Returns the original for greys/blacks."""
    if not hex_colour or len(hex_colour) != 7:
        return img
    target = np.array([int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255
    th, ts, tv = _rgb_to_hsv(target[None])
    th, ts, tv = float(th[0]), float(ts[0]), float(tv[0])
    if ts < 0.28 or tv < 0.25:
        return img  # monochrome brand: keep the plate's own warm light
    a = np.asarray(img, np.float32) / 255
    h, s, v = _rgb_to_hsv(a)
    # Warm band: red-orange through gold (≈ 8°–60°), softly feathered.
    warm = np.clip(1 - np.abs(((h - 0.093 + 0.5) % 1) - 0.5) / 0.075, 0, 1)
    weight = warm * np.clip((s - 0.18) / 0.3, 0, 1) * np.clip((v - 0.04) / 0.12, 0, 1)
    # Light falls off smoothly: blur the mask so wood grain and noise don't
    # make it switch on and off pixel by pixel (that read as patchy bands).
    w_img = Image.fromarray((weight * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(18))
    weight = np.maximum(weight * 0.35, np.asarray(w_img, np.float32) / 255)
    # Light sources are near-white at the core: keep a little of the target
    # hue there too so a lamp reads as red, not white with red edges.
    new = _hsv_to_rgb(np.full_like(h, th), np.clip(s * (0.75 + 0.5 * ts), 0, 1), v)
    out = a * (1 - weight[..., None]) + new * weight[..., None]
    return Image.fromarray(np.clip(out * 255, 0, 255).astype(np.uint8))


def render_hook(text, width, top, bottom, dark=False):
    """The POV line above the screen: centred, white with a soft shadow, or
    near-black on a bright room (a daylight wall) so it stays readable.
    Returns an RGBA layer the size of the output frame."""
    from PIL import ImageFont
    k = width / 1080  # sizes below are for a 1080-wide frame; the master is 4K
    layer = Image.new("RGBA", (width, bottom + round(40 * k)), (0, 0, 0, 0))
    if not text:
        return layer
    path = str(HERE / "fonts" / "InterTight.ttf")
    size = round(66 * k)
    while True:
        font = ImageFont.truetype(path, size)
        try:
            font.set_variation_by_axes([640])
        except Exception:
            pass
        # Wrap to the safe width, keeping any line breaks the hook asks for.
        lines = []
        for para in text.split("\n"):
            words, line = para.split(), ""
            for w in words:
                trial = (line + " " + w).strip()
                if font.getlength(trial) <= width * 0.84 or not line:
                    line = trial
                else:
                    lines.append(line); line = w
            lines.append(line)
        lh = int(size * 1.22)
        if len(lines) * lh <= bottom - top or size <= 40 * k:
            break
        size -= round(4 * k)
    block = len(lines) * lh
    y = top + max(0, (bottom - top - block) // 2)
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ds, dt = ImageDraw.Draw(shadow), ImageDraw.Draw(layer)
    for i, line in enumerate(lines):
        x = (width - font.getlength(line)) / 2
        ds.text((x, y + i * lh + round(4 * k)), line, font=font, fill=(255, 255, 255, 120) if dark else (0, 0, 0, 150))
        dt.text((x, y + i * lh), line, font=font, fill=(20, 20, 22, 255) if dark else (255, 255, 255, 255))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6 * k))
    return Image.alpha_composite(shadow, layer)


def qh_half(c):
    return c.screen_h / 2


def _ease(t):
    return t * t * (3 - 2 * t)


class MacbookCompositor:
    def __init__(self, plate, src_size, out_size=(1080, 1920), push_in=0.07, fill=0.84, light=None, hook=None):
        self.plate = relight(Image.open(HERE / plate["file"]).convert("RGB"), light)
        self.out_size = out_size
        self.push_in = push_in
        W, H = self.plate.size
        q = plate["screen"]
        quad = [q["tl"], q["tr"], q["br"], q["bl"]]
        # Grow the screen quad ~1.5px so the plate's white panel never peeks
        # out as a bright hairline around the site.
        mx, my = sum(p[0] for p in quad) / 4, sum(p[1] for p in quad) / 4
        quad = [(x + (1.5 if x > mx else -1.5), y + (1.5 if y > my else -1.5)) for x, y in quad]
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        qw, qh = max(xs) - min(xs), max(ys) - min(ys)
        self.center = (sum(xs) / 4, sum(ys) / 4)
        self.screen_h = qh

        # Working region: the screen plus room for bloom and the light spill
        # down over the keyboard and desk. Everything outside stays the plate.
        pad = int(qw * 0.09)
        self.rx0, self.ry0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
        self.rx1, self.ry1 = min(W, int(max(xs)) + pad), min(H, int(max(ys) + qh * 0.95))
        rw, rh = self.rx1 - self.rx0, self.ry1 - self.ry0
        local = [(x - self.rx0, y - self.ry0) for x, y in quad]
        self.region_size = (rw, rh)

        sw, sh = src_size
        self.mesh = None
        outline = local
        if "top_mid" in q and "bottom_mid" in q:
            # Curved screen (an ultrawide, say): the top and bottom edges bow,
            # so the site is laid on in thin strips that follow the curve.
            tm, bm = q["top_mid"], q["bottom_mid"]
            tm = (tm[0] - self.rx0, tm[1] - 1.5 - self.ry0)
            bm = (bm[0] - self.rx0, bm[1] + 1.5 - self.ry0)
            top = _bezier(local[0], tm, local[1], 49)
            bottom = _bezier(local[3], bm, local[2], 49)
            self.mesh, outline = curved_mesh(top, bottom, src_size, (rw, rh))
        else:
            self.coeffs = _perspective_coeffs(local, [(0, 0), (sw, 0), (sw, sh), (0, sh)])

        # Anti-aliased screen mask (drawn 4× and reduced).
        big = Image.new("L", (rw * 4, rh * 4), 0)
        ImageDraw.Draw(big).polygon([(x * 4, y * 4) for x, y in outline], fill=255)
        self.mask = np.asarray(big.resize((rw, rh), Image.Resampling.LANCZOS), np.float32)[..., None] / 255

        # Faint diagonal reflection on the glass.
        gy, gx = np.mgrid[0:rh, 0:rw].astype(np.float32)
        diag = (gx / rw * 0.6 + gy / rh * 0.4)
        self.glass = (np.clip(1 - np.abs(diag - 0.32) / 0.22, 0, 1) ** 2 * 9.0)[..., None]

        # Where the screen's light lands: an ellipse under the screen.
        cx, cy = self.center[0] - self.rx0, max(ys) - self.ry0 + qh * 0.32
        spill = np.exp(-(((gx - cx) / (qw * 0.62)) ** 2 + ((gy - cy) / (qh * 0.55)) ** 2))
        spill[gy < (min(ys) - self.ry0)] = 0
        # Fade to nothing well inside the working region: the plate outside it
        # is untouched, so any light change must reach zero before the edge
        # (a hard stop here showed as a seam across the desk and laptop).
        feather = max(40.0, qw * 0.08)
        edge = np.minimum.reduce([gx, rw - 1 - gx, gy, rh - 1 - gy]) / feather
        spill *= np.clip(edge, 0, 1) ** 2
        self.spill = (spill * (1 - self.mask[..., 0]))[..., None].astype(np.float32)
        self.edge_fade = np.clip(edge, 0, 1)[..., None].astype(np.float32)

        self.base = np.asarray(self.plate.crop((self.rx0, self.ry0, self.rx1, self.ry1)), np.float32)

        # Crop window: the output aspect, framed so the whole laptop (up to 1.45×
        # the screen width) fills `fill` of the frame, and still fits after the push-in.
        ow, oh = out_size
        if W / H > ow / oh:
            full = (H * ow / oh, H)
        else:
            full = (W, W * oh / ow)
        base_zoom = max(1.0, full[0] * fill / (qw * 1.45))
        self.win = (full[0] / base_zoom, full[1] / base_zoom)

        # Hook text sits between Instagram's top bar and the laptop lid, at
        # its highest point during the push-in.
        lid_top = min(ys) - qh * 0.04
        lid_out = min(self._to_output_y(lid_top, t) for t in (0.0, 1.0))
        top, bottom = int(oh * 0.13), int(lid_out - oh * 0.035)
        dark = False
        if hook:
            # How bright the room is where the hook sits: a white wall needs dark text.
            view = np.asarray(self.plate.resize(out_size, Image.Resampling.BILINEAR, box=self._box(0.0)).convert("L"), np.float32)
            dark = float(view[top:max(top + 1, bottom), int(ow * 0.1):int(ow * 0.9)].mean()) > 165
        self.hook_layer = render_hook(hook, ow, top, bottom, dark=dark) if hook else None

    def _box(self, t):
        z = 1 + self.push_in * _ease(t)
        ww, wh = self.win[0] / z, self.win[1] / z
        W, H = self.plate.size
        cx = min(max(self.center[0], ww / 2), W - ww / 2)
        cy = min(max(self.center[1] + qh_half(self) * 0.35, wh / 2), H - wh / 2)
        return (cx - ww / 2, cy - wh / 2, cx + ww / 2, cy + wh / 2)

    def _to_output_y(self, y, t):
        x0, y0, x1, y1 = self._box(t)
        return (y - y0) / (y1 - y0) * self.out_size[1]

    def frame(self, src, t, seconds=None):
        """src: PIL RGB screen capture. t: 0..1 through the Reel. Returns RGB bytes."""
        rw, rh = self.region_size
        if self.mesh is not None:
            warped = np.asarray(src.transform((rw, rh), Image.Transform.MESH, self.mesh, Image.Resampling.BICUBIC), np.float32)
        else:
            warped = np.asarray(src.transform((rw, rh), Image.Transform.PERSPECTIVE, self.coeffs, Image.Resampling.BICUBIC), np.float32)
        # A lit display in a dark room: lifted blacks, a touch under full white.
        screen = 9 + warped * (238 / 255)

        inside = self.mask[..., 0] > 0.5
        mean = screen[inside].mean(axis=0) if inside.any() else np.array([200, 200, 200], np.float32)
        lum = float(mean.mean()) / 255

        region = self.base.copy()
        # The plate was lit by a white screen: dim and tint that light to match.
        dim = 0.62 + 0.38 * lum
        region *= 1 - self.spill * (1 - dim)
        tint = (mean / max(mean.mean(), 1) - 1) * 26
        region += self.spill * tint

        region = region * (1 - self.mask) + (screen + self.glass) * self.mask

        # Bloom: the bright screen bleeding softly onto the bezel.
        lit = Image.fromarray(np.clip(screen * self.mask, 0, 255).astype(np.uint8))
        small = lit.resize((max(1, rw // 8), max(1, rh // 8)), Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(5))
        bloom = np.asarray(small.resize((rw, rh), Image.Resampling.BILINEAR), np.float32) * self.edge_fade
        region = 255 - (255 - region) * (1 - bloom / 255 * 0.16)

        img = self.plate.copy()
        img.paste(Image.fromarray(np.clip(region, 0, 255).astype(np.uint8)), (self.rx0, self.ry0))

        # Slow push-in toward the screen, keeping the whole laptop framed.
        # Lanczos: the plate is scaled up a little for the push-in, and
        # bilinear left it visibly soft on a phone.
        out = img.resize(self.out_size, Image.Resampling.LANCZOS, box=self._box(t))
        if self.hook_layer is not None:
            # Fade the hook in over the first half-second.
            a = 1.0 if seconds is None else min(1.0, max(0.0, (seconds - 0.15) / 0.45))
            if a > 0:
                layer = self.hook_layer if a >= 1 else Image.fromarray((np.asarray(self.hook_layer) * np.array([1, 1, 1, a])).astype(np.uint8))
                out.paste(layer, (0, 0), layer)
        return out.tobytes()
