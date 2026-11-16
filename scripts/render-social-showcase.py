"""Render an accurate homepage mockup and a full-screen, animated site-scroll Reel.

Usage: python scripts/render-social-showcase.py <manifest.json> <output-dir>
The GitHub worker installs Playwright Chromium, Pillow and ffmpeg first.
"""

import base64
import colorsys
import ipaddress
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from playwright.sync_api import sync_playwright


MANIFEST = json.loads(Path(sys.argv[1]).read_text())
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
FRAMES = OUT / "frames"
FRAMES.mkdir(exist_ok=True)
# 60 fps, like a phone's own screen recording. Blending sub-frames into 30 fps
# was tried: at scrolling speed it reads as doubled text, not motion blur.
FPS = 60
# Optional third argument caps the Reel length in seconds (handy for tests).
MAX_SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 36.0
HOLD_START, HOLD_END, RAMP = 1.0, 1.0, 0.7
# Reel pacing, in screens per second and seconds (see reel_timeline).
REEL_TARGET_SECONDS, SPEED_MIN, SPEED_MAX = 20.0, 0.8, 1.8
# Wheel-scrolled (WebGL) sites: no page length to fit, so a set pace.
WHEEL_SPEED = 1.1
# The Reel shows the desktop site on a MacBook in a moody room
# (scripts/macbook). 1512 × 982 is a 14" MacBook Pro's default viewport.
REEL_VIEWPORT = {"width": 1512, "height": 982}
REEL_SCALE = 1  # set per site in capture(): 2 (retina) for normal pages, 1 for WebGL
# The Reel is composited as a 4K master; a 1080p copy is made from it for the
# platforms (Instagram's API takes at most 1920 px wide, and plays 1080p).
MASTER = (2160, 3840)
# The look (Zach, 15 Nov: "give it vibrance, make it feel super eye catching"):
# vibrance lifts the muted colours more than the already-strong ones, so skin
# and brand colours don't go neon; a touch of contrast and a fine sharpen.
GRADE = "vibrance=intensity=0.4,eq=contrast=1.08:saturation=1.06,unsharp=5:5:0.45"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
if not Path(FONT).exists():
    FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"
    BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def safe_host(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "metadata.google.internal") or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return bool(host and "." in host)


def rgb(hex_color):
    return tuple(int(hex_color[i:i+2], 16) for i in (1, 3, 5))


def useful_css_color(value):
    if not value:
        return None
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        colour = rgb(value)
    else:
        match = re.match(r"rgba?\((\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)", value)
        if not match:
            return None
        colour = tuple(map(int, match.groups()))
    spread = max(colour) - min(colour)
    if spread < 35 or max(colour) < 65 or min(colour) > 225:
        return None
    return "#" + "".join(f"{part:02x}" for part in colour)


def font(size, bold=False):
    return ImageFont.truetype(BOLD if bold else FONT, size)


def gradient(size, colour):
    # A light mesh gradient keeps the site's hue without turning the post into
    # a flat brand-colour tile. Render small, then upscale for a smooth finish.
    small = (size[0] // 4, size[1] // 4)
    base = Image.new("RGB", small)
    px = base.load()
    hue, _, _ = colorsys.rgb_to_hls(*(part / 255 for part in colour))
    companion = tuple(round(part * 255) for part in colorsys.hls_to_rgb((hue + .11) % 1, .88, .34))
    primary = tuple(round(part * .30 + 255 * .70) for part in colour)
    ivory = (250, 248, 245)
    blush = (255, 238, 234)
    for y in range(small[1]):
        ny = y / small[1]
        for x in range(small[0]):
            nx = x / small[0]
            a = 2.0 * math.exp(-((nx - .08) ** 2 / .24 + (ny - .18) ** 2 / .20))
            b = 1.6 * math.exp(-((nx - .96) ** 2 / .23 + (ny - .78) ** 2 / .24))
            c = .55 * math.exp(-((nx - .84) ** 2 / .28 + (ny - .05) ** 2 / .25))
            px[x, y] = tuple(round((ivory[i] + primary[i] * a + companion[i] * b + blush[i] * c) / (1 + a + b + c)) for i in range(3))
    return base.resize(size, Image.Resampling.BICUBIC)


def crop_fit(path, size):
    im = Image.open(path).convert("RGB")
    w, h = size
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = round(sw * scale), round(sh * scale)
    im = im.resize((nw, nh), Image.Resampling.LANCZOS)
    return im.crop(((nw-w)//2, 0, (nw-w)//2+w, h))


def rounded_paste(base, source, xy, radius):
    mask = Image.new("L", source.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, source.width-1, source.height-1), radius=radius, fill=255)
    base.paste(source, xy, mask)


def draw_label(draw, text, xy, max_width, size=52):
    while size > 25 and draw.textlength(text, font=font(size, True)) > max_width:
        size -= 2
    draw.text(xy, text, font=font(size, True), fill="#203039")


def make_mockup(colour):
    im = gradient((1080, 1350), colour)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((78, 78, 119, 87), radius=4, fill=colour)
    d.text((78, 108), "WEBSITE SHOWCASE", font=font(22, True), fill="#485B63")
    draw_label(d, MANIFEST["business_name"], (78, 151), 925, 58)

    shadow = Image.new("RGBA", im.size)
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((70, 350, 1009, 965), radius=30, fill=(26, 43, 55, 95))
    sd.rounded_rectangle((658, 548, 977, 1229), radius=52, fill=(26, 43, 55, 105))
    im = Image.alpha_composite(im.convert("RGBA"), shadow.filter(ImageFilter.GaussianBlur(34)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((70, 350, 1009, 929), radius=28, fill="#202124")
    d.rounded_rectangle((80, 360, 999, 921), radius=20, fill="#08090b")
    rounded_paste(im, crop_fit(OUT / "desktop.png", (899, 541)), (90, 371), 10)
    d = ImageDraw.Draw(im)
    d.ellipse((535, 355, 545, 365), fill="#55585d")
    d.polygon([(55, 929), (1024, 929), (1050, 965), (30, 965)], fill="#b4b7ba")
    d.polygon([(55, 929), (1024, 929), (1009, 948), (72, 948)], fill="#dddfe1")

    d.rounded_rectangle((660, 547, 975, 1227), radius=49, fill="#111215", outline="#4b4c50", width=3)
    rounded_paste(im, crop_fit(OUT / "mobile.png", (289, 650)), (673, 560), 39)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((775, 566, 860, 585), radius=10, fill="#070709")
    d.text((78, 1267), "BUILT BY BLUEPEEK", font=font(20, True), fill="#203039")
    d.text((772, 1267), "DESKTOP + MOBILE", font=font(16), fill="#485B63")
    im.convert("RGB").save(OUT / "showcase.png")
    im.convert("RGB").save(OUT / "showcase.jpg", quality=93, subsampling=0)


# The site runs on a clock the renderer controls. Until __vclock.start() it is
# the real clock. After it, time only moves when the renderer steps it exactly
# one capture frame, so damping, GSAP scrubs, Lenis, timers and CSS animations
# all advance by the same amount between every pair of frames, however long a
# screenshot takes. That is what removes the judder of real-time capture.
VIRTUAL_CLOCK = """(() => {
  const realNow = performance.now.bind(performance);
  const realDateNow = Date.now;
  const realRaf = window.requestAnimationFrame.bind(window);
  const realCancelRaf = window.cancelAnimationFrame.bind(window);
  const realSetTimeout = window.setTimeout.bind(window);
  const realClearTimeout = window.clearTimeout.bind(window);
  const realSetInterval = window.setInterval.bind(window);
  const realClearInterval = window.clearInterval.bind(window);
  let on = false, now = 0, dateBase = 0, nextId = 1e6;
  let rafs = new Map();
  const timers = new Map();
  const seen = new WeakSet();

  performance.now = () => on ? now : realNow();
  Date.now = () => on ? Math.round(dateBase + now) : realDateNow();
  window.requestAnimationFrame = (cb) => {
    if (!on) return realRaf(cb);
    const id = nextId++; rafs.set(id, cb); return id;
  };
  window.cancelAnimationFrame = (id) => { rafs.delete(id); realCancelRaf(id); };
  const addTimer = (cb, ms, args, every) => {
    const id = nextId++;
    timers.set(id, { cb, args, due: now + Math.max(0, +ms || 0), every: every ? Math.max(1, +ms || 0) : 0 });
    return id;
  };
  window.setTimeout = (cb, ms, ...args) => on ? addTimer(cb, ms, args, false) : realSetTimeout(cb, ms, ...args);
  window.setInterval = (cb, ms, ...args) => on ? addTimer(cb, ms, args, true) : realSetInterval(cb, ms, ...args);
  window.clearTimeout = (id) => { timers.delete(id); realClearTimeout(id); };
  window.clearInterval = (id) => { timers.delete(id); realClearInterval(id); };

  const run = (cb, args) => { try { typeof cb === "function" ? cb(...(args || [])) : null; } catch (e) { console.error(e); } };
  const pauseAnimations = (fresh) => {
    for (const a of document.getAnimations()) {
      if (!seen.has(a)) {
        seen.add(a);
        // Running at start() keeps its phase; anything newer starts at zero.
        a.__vt = fresh ? 0 : (a.currentTime || 0);
        a.pause();
      }
    }
  };

  window.__vclock = {
    start() {
      if (on) return;
      now = realNow(); dateBase = realDateNow() - now; on = true;
      pauseAnimations(false);
    },
    async step(ms) {
      now += ms;
      for (const [id, t] of [...timers].sort((a, b) => a[1].due - b[1].due)) {
        if (t.due > now || !timers.has(id)) continue;
        if (t.every) { while (t.due <= now) t.due += t.every; } else timers.delete(id);
        run(t.cb, t.args);
      }
      const batch = rafs; rafs = new Map();
      for (const cb of batch.values()) run(cb, [now]);
      pauseAnimations(true);
      for (const a of document.getAnimations()) {
        if (a.__vt === undefined) continue;
        a.__vt += ms;
        try { a.currentTime = a.__vt; } catch (e) {}
      }
    },
    // Resolves once every video has decoded the frame the site asked for and
    // the browser has painted it.
    async settle() {
      const deadline = realNow() + 4000;
      while (realNow() < deadline) {
        const vids = [...document.querySelectorAll("video")].filter(v => v.isConnected && v.getClientRects().length);
        if (vids.every(v => !v.seeking && (v.readyState >= 2 || v.networkState === 3 || !v.currentSrc))) break;
        await new Promise(r => realSetTimeout(r, 8));
      }
      await new Promise(r => realRaf(() => realRaf(r)));
    },
  };
})();"""


def reel_viewport(plate):
    """
    The browser size for a plate's screen: its exact proportions, 1512 px wide
    (a 14" MacBook Pro), but never shorter than 860 px. An ultrawide at 1512
    wide is only ~530 px tall, which crushes a site's full-height sections
    into a strip (Zach, 15 Nov: "the monitor screen is super squished"); a
    real ultrawide is a wide desktop, so it gets a wider viewport instead.
    """
    aspect = plate.get("screen_aspect", 1512 / 982)
    width = max(1512, round(860 * aspect))
    return {"width": width, "height": round(width / aspect)}


def reel_timeline(bottom):
    """Scroll positions for each capture frame: hold, glide, hold.

    The glide moves at one steady speed with short eased ramps at each end,
    so the viewer never sees the page lurch or crawl.
    """
    viewport = REEL_VIEWPORT["height"]
    # Speed fits the page: the whole site in a ~20 second Reel. Short pages
    # still move at 0.8 of a screen a second (half a screen dragged: Zach, 15 Nov,
    # "the scroll was quite slow"); long ones go up to 1.8 screens a second
    # so they reach the footer, and past that end part-way down rather than blur.
    longest = min(MAX_SECONDS, REEL_TARGET_SECONDS) - HOLD_START - HOLD_END
    speed = min(max(bottom / max(1.0, longest - RAMP), viewport * SPEED_MIN), viewport * SPEED_MAX)
    travel = min(bottom, speed * (longest - RAMP))
    glide = max(travel / speed + RAMP, 2 * RAMP + 0.5)
    total = HOLD_START + glide + HOLD_END
    cruise = glide - RAMP  # distance-equivalent seconds at full speed
    positions = []
    for i in range(round(total * FPS)):
        t = i / FPS - HOLD_START
        if t <= 0:
            d = 0.0
        elif t >= glide:
            d = cruise
        elif t < RAMP:
            d = t * t / (2 * RAMP)
        elif t > glide - RAMP:
            r = glide - t
            d = cruise - r * r / (2 * RAMP)
        else:
            d = t - RAMP / 2
        positions.append(travel * d / cruise)
    return positions


# Cookie/consent banners: the final check holds any reel that shows one
# (16 Nov), so accept them the way a visitor would, then hide whatever's left.
COOKIE_CSS = """
#onetrust-banner-sdk, #onetrust-consent-sdk, #CybotCookiebotDialog, #CybotCookiebotDialogBodyUnderlay,
#usercentrics-root, #usercentrics-cmp-ui, #didomi-host, #didomi-popup, .didomi-popup-backdrop,
#truste-consent-track, #consent_blackbar, .cc-window, .cc-banner, .cky-consent-container, .cky-overlay,
#cookie-law-info-bar, .cli-modal-backdrop, #moove_gdpr_cookie_info_bar, .cmplz-cookiebanner, #cmplz-cookiebanner-container,
#hs-eu-cookie-confirmation, .iubenda-cs-container, #iubenda-cs-banner, #qc-cmp2-container, .qc-cmp2-container,
#termly-code-snippet-support, .termly-styles-root, [id^="sp_message_container"], .osano-cm-window, #ccc,
.fc-consent-root, #cookiescript_injected, #cookie-banner, #cookie-notice, #cookieConsent, #cookie-consent,
.cookie-banner, .cookie-notice, .cookie-consent, .cookies-banner, [aria-label*="cookie" i][role="dialog"],
[aria-label*="consent" i][role="dialog"] { display: none !important; visibility: hidden !important; }
html, body { overflow: auto !important; }
"""

DISMISS_COOKIES = """() => {
  const words = /^(accept( all| all cookies| cookies)?|allow( all| all cookies| cookies)?|agree( and close)?|i agree|ok(ay)?|got it|understood|i understand|continue|close|dismiss|alle akzeptieren|akzeptieren|tout accepter|accepter|aceptar( todo)?|accetta( tutto)?)$/i;
  const host = /cookie|consent|gdpr|privacy|cmp|onetrust|didomi|usercentrics|cookiebot|osano|iubenda|termly|truste/i;
  const inBanner = (el) => { for (let n = el; n && n !== document.body; n = n.parentElement) { if (host.test((n.id || '') + ' ' + (n.className?.baseVal ?? n.className ?? '') + ' ' + (n.getAttribute('aria-label') || ''))) return true; } return false; };
  let clicked = 0;
  for (const el of document.querySelectorAll('button, a[role="button"], [role="button"], input[type="button"], input[type="submit"]')) {
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    if (text.length > 40) continue;
    const exactAccept = /^(accept all( cookies)?|allow all( cookies)?|accept cookies|alle akzeptieren|tout accepter)$/i.test(text);
    if ((exactAccept || (words.test(text) && inBanner(el))) && el.offsetParent !== null) { el.click(); clicked++; }
  }
  // Whatever's left: a fixed/sticky bar that talks about cookies is a consent banner.
  for (const el of document.body.querySelectorAll('*')) {
    const pos = getComputedStyle(el).position;
    if (pos !== 'fixed' && pos !== 'sticky') continue;
    const t = el.innerText || '';
    if (t.length < 700 && /cookie|consent|gdpr/i.test(t)) { el.style.setProperty('display', 'none', 'important'); clicked++; }
  }
  return clicked;
}"""


def dismiss_cookies_now(page):
    """The in-recording version: no waits, main page only."""
    try:
        page.evaluate(DISMISS_COOKIES)
    except Exception:
        pass


def dismiss_cookies(page):
    """Click a cookie banner's accept button (main page and consent iframes), then hide known banners."""
    try:
        for frame in page.frames:
            try:
                frame.evaluate(DISMISS_COOKIES)
            except Exception:
                pass
        page.wait_for_timeout(600)
        page.add_style_tag(content=COOKIE_CSS)
    except Exception:
        pass  # never let a banner helper fail the render
# Cookie banners and chat bubbles sat on top of every shot (Heimdall Power,
# 16 Nov) and the reviewer rightly failed the site. Hide them with CSS from the
# first paint: nothing is accepted or clicked, they just never show.
HIDE_OVERLAYS = r"""
(() => {
  const css = `
    #CybotCookiebotDialog, #CybotCookiebotDialogBodyUnderlay, #onetrust-consent-sdk, #onetrust-banner-sdk, .onetrust-pc-dark-filter,
    #usercentrics-root, #usercentrics-cmp-ui, .cky-consent-container, .cky-overlay, .cky-btn-revisit-wrapper, #cookie-law-info-bar, .cli-modal-backdrop,
    #cmplz-cookiebanner-container, .cmplz-cookiebanner, #didomi-host, #iubenda-cs-banner, #termly-code-snippet-support,
    #cookiescript_injected, #cookiescript_injected_wrapper, .cc-window, .cc-revoke, #cookie-notice, #cookie-banner, .cookie-banner, #CookieBanner,
    #hs-eu-cookie-confirmation, #moove_gdpr_cookie_info_bar, .osano-cm-window, .osano-cm-dialog, #truste-consent-track, #qc-cmp2-container,
    .fc-consent-root, #axeptio_overlay, #tarteaucitronRoot, #klaro, .klaro, #ccc, #cookiefirst-root, .cookiefirst-root, #BorlabsCookieBox,
    [class*="cookie-consent" i], [id*="cookie-consent" i], [class*="cookie-banner" i], [id*="cookie-banner" i], [class*="cookie-notice" i], [id*="cookie-notice" i],
    [class*="cookieconsent" i], [id*="cookieconsent" i], [aria-label*="cookie" i][role="dialog"], [aria-label*="cookie" i][role="region"],
    #intercom-container, .intercom-lightweight-app, .intercom-launcher, #hubspot-messages-iframe-container, #drift-widget, #drift-frame-controller,
    #drift-frame-chat, .crisp-client, #crisp-chatbox, iframe#launcher, #webWidget, .zsiq_floatmain, .zsiq_float, #chat-widget-container,
    #fc_frame, #tidio-chat, #gorgias-chat-container, #beacon-container, .tawk-min-container, iframe[title*="chat" i], iframe[title*="messaging" i]
    { display: none !important; visibility: hidden !important; opacity: 0 !important; pointer-events: none !important; }`;
  const add = () => {
    if (document.getElementById("__bp_hide")) return;
    const style = document.createElement("style");
    style.id = "__bp_hide";
    style.textContent = css;
    (document.head || document.documentElement).appendChild(style);
  };
  if (document.documentElement) add();
  document.addEventListener("DOMContentLoaded", add);
})();
"""

# A full-screen loader or intro covering the page (Delta Cafés, Obys, 16 Nov:
# the first shots were the loader). Wait for it to go, up to a limit.
LOADER_GONE = r"""
() => {
  const vw = innerWidth, vh = innerHeight;
  for (const el of document.querySelectorAll("body *")) {
    const name = ((el.id || "") + " " + (typeof el.className === "string" ? el.className : "")).toLowerCase();
    if (!/(^|[\s_-])(pre-?loader|loader|loading|loading-screen|intro|splash|curtain)([\s_-]|$)/.test(name)) continue;
    const st = getComputedStyle(el);
    if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) < 0.1) continue;
    if (st.position !== "fixed" && st.position !== "absolute") continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height >= vw * vh * 0.6 && r.top < vh && r.bottom > 0) return false;
  }
  return true;
}
"""


def prepare_context(ctx):
    """Every browser context the reel uses hides cookie and chat overlays."""
    ctx.add_init_script(HIDE_OVERLAYS)


def wait_for_loader(page, limit_ms=12000):
    """After the usual wait, give a still-showing full-screen loader up to `limit_ms` more to finish."""
    try:
        page.wait_for_function(LOADER_GONE, timeout=limit_ms, polling=500)
    except Exception:
        print("A loader was still showing after the wait; carrying on", flush=True)
    else:
        page.wait_for_timeout(600)  # its fade-out


def shot(page, path, previous=None, **kwargs):
    """
    One capture frame. A heavy site can stall a single screenshot past
    Playwright's 30 s (Locomotive, 16 Nov), which used to kill the whole
    render: try once more with longer, then reuse the previous frame.
    """
    for timeout in (30000, 90000):
        try:
            page.screenshot(path=str(path), timeout=timeout, **kwargs)
            return
        except Exception as e:
            print(f"Screenshot {Path(path).name} stalled ({type(e).__name__}), {'retrying' if timeout == 30000 else 'reusing the last frame'}", flush=True)
    if previous and Path(previous).exists():
        import shutil
        shutil.copyfile(previous, path)
        return
    raise RuntimeError("The site is too heavy to record (screenshots kept stalling)")


def open_site(page, url, attempts=3):
    """Load the site and make sure a real HTML page with a body is there.

    A GitHub runner sometimes got a document with no <body> (a block page,
    redirect or non-HTML response for a US datacentre visitor), and the next
    page read crashed with a cryptic getComputedStyle error. Wait for load,
    check, retry; if it never loads, say so plainly.
    """
    last = None
    for attempt in range(attempts):
        if attempt:
            page.wait_for_timeout(3000 * attempt)
        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass  # heavy pages: carry on once the DOM is there
        state = page.evaluate("() => ({body: !!document.body && document.body.children.length > 0, type: document.contentType, url: location.href})")
        if state["body"]:
            page.wait_for_timeout(1500)  # consent tools inject their banner a moment after load
            dismiss_cookies(page)
            return response
        last = (response.status if response else None, state["url"], state["type"])
    status, final_url, kind = last
    raise RuntimeError(f"The site didn't load for the renderer (status {status}, {kind}, ended at {final_url}). It may be blocking overseas or automated visitors.")


def capture():
    # Capture at the chosen MacBook screen's exact proportions (1512 px wide,
    # the 14" MacBook Pro viewport) so the site fills the glass unstretched.
    global REEL_VIEWPORT
    sys.path.insert(0, str(Path(__file__).parent / "macbook"))
    from composite import choose_plate
    plate = choose_plate(MANIFEST.get("id") or MANIFEST["site_url"], MANIFEST.get("reel_plate"))
    REEL_VIEWPORT = reel_viewport(plate)
    site = MANIFEST["site_url"]
    if not safe_host(site):
        raise ValueError("Only public HTTPS websites can be captured")
    with sync_playwright() as p:
        # Google Chrome, not Playwright's Chromium: Chromium builds ship
        # without H.264, so a site's MP4 hero/scroll film never decodes and
        # the Reel shows a frozen poster.
        browser = p.chromium.launch(headless=True, channel="chrome", args=["--no-sandbox", "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required"])
        desktop_context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, reduced_motion="reduce")
        mobile_context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True, has_touch=True, reduced_motion="reduce")
        for ctx in (desktop_context, mobile_context):
            prepare_context(ctx)
            ctx.route("**/*", lambda route: route.continue_() if safe_host(route.request.url) else route.abort())

        desktop = desktop_context.new_page()
        open_site(desktop, site)
        desktop.wait_for_timeout(3500)
        wait_for_loader(desktop, 8000)
        desktop.screenshot(path=str(OUT / "desktop.png"), animations="disabled")
        # Retina capture for normal scrolling pages, so text on the screen stays
        # crisp in the 4K master. WebGL sites stay at 1x: without a GPU they
        # already take ~2 s a frame, and 2x would be four times the pixels.
        global REEL_SCALE
        REEL_SCALE = 2 if desktop.evaluate("document.documentElement.scrollHeight - innerHeight") >= 300 else 1
        desktop.close()

        mobile = mobile_context.new_page()
        open_site(mobile, site)
        mobile.wait_for_timeout(4000)
        wait_for_loader(mobile, 8000)
        if MANIFEST["accent_color"] == "#315cff":
            try:
                candidates = mobile.evaluate("""() => {
                  const bg = (el) => (el instanceof Element ? getComputedStyle(el).backgroundColor : null);
                  return [
                    document.querySelector('meta[name="theme-color"]')?.content,
                    ...Array.from(document.querySelectorAll('button, a')).slice(0, 40).map(bg),
                    bg(document.querySelector('header') || document.body),
                  ];
                }""")
            except Exception:
                candidates = []  # colour falls back to the screenshot / plate amber
            detected = next((colour for raw in candidates if (colour := useful_css_color(raw))), None)
            if detected:
                MANIFEST["accent_color"] = detected
        mobile.screenshot(path=str(OUT / "mobile.png"), animations="disabled")
        reel_context = browser.new_context(viewport=REEL_VIEWPORT, device_scale_factor=REEL_SCALE, reduced_motion="no-preference")
        reel_context.add_init_script(VIRTUAL_CLOCK)
        prepare_context(reel_context)
        reel_context.route("**/*", lambda route: route.continue_() if safe_host(route.request.url) else route.abort())
        print(f"Recording at {REEL_VIEWPORT['width']}x{REEL_VIEWPORT['height']} @{REEL_SCALE}x", flush=True)
        # Warm the site's lazy-loaded assets in this context, then open a fresh
        # page so one-time scroll animations can play during the Reel.
        warmup = reel_context.new_page()
        open_site(warmup, site)
        warmup.evaluate("""async () => {
          const bottom = Math.max(0, document.documentElement.scrollHeight - innerHeight);
          for (let i = 1; i <= 12; i++) {
            scrollTo(0, bottom * i / 12);
            await new Promise(r => setTimeout(r, 150));
          }
        }""")
        warmup.close()

        reel = reel_context.new_page()
        open_site(reel, site)
        try:
            reel.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # Let loaders, intros and scroll holds finish in real time first.
        reel.wait_for_timeout(4500)
        wait_for_loader(reel)
        dismiss_cookies(reel)
        reel.evaluate("""async () => {
          await document.fonts?.ready;
          document.documentElement.style.scrollBehavior = 'auto';
          scrollTo(0, 0);
        }""")
        reel.wait_for_timeout(500)
        bottom = reel.evaluate("Math.max(0, document.documentElement.scrollHeight - innerHeight)")
        step_ms = 1000 / FPS
        if bottom < 300:
            # No page scroll: a WebGL or scroll-jacked site (common among award
            # winners) that moves on wheel events instead. Scroll it the way a
            # visitor does, a steady wheel at the same readable pace.
            wheel_reel(reel, step_ms)
            browser.close()
            return
        positions = reel_timeline(bottom)
        reel.evaluate("window.__vclock.start()")
        for i, y in enumerate(positions):
            reel.evaluate("""async ([y, ms]) => {
              const top = Math.round(Math.min(y, Math.max(0, document.documentElement.scrollHeight - innerHeight)));
              if (window.lenis?.scrollTo) window.lenis.scrollTo(top, { immediate: true, force: true });
              else window.scrollTo({ top, behavior: 'instant' });
              await window.__vclock.step(ms);
              await window.__vclock.settle();
            }""", [y, step_ms])
            if i % 10 == 0:
                dismiss_cookies_now(reel)  # banners that appear once the virtual clock runs
            # JPEG: much faster to write, and invisible once shrunk onto the laptop screen.
            shot(reel, FRAMES / f"{i:05d}.jpg", FRAMES / f"{i - 1:05d}.jpg" if i else None, type="jpeg", quality=92, animations="allow", caret="hide")
            if i % 120 == 0:
                print(f"Captured {i}/{len(positions)} scroll frames", flush=True)
        browser.close()


def _motion(paths):
    """How different consecutive frames are (0-255 mean change on a small greyscale thumbnail)."""
    import numpy as np
    thumbs = [np.asarray(Image.open(p).convert("L").resize((96, 60)), np.float32) for p in paths]
    return [round(float(np.abs(a - b).mean()), 2) for a, b in zip(thumbs, thumbs[1:])]


def preflight():
    """
    A quick look before the full render: open the site the way the Reel will,
    scroll through it in 8 steps and save a screenshot at each, plus how much
    the screen changed. The worker sends these to the reviewer, which decides
    whether the site is worth a full render (no stuck loaders, no blank WebGL,
    a real site to scroll through).
    """
    global REEL_VIEWPORT
    sys.path.insert(0, str(Path(__file__).parent / "macbook"))
    from composite import choose_plate
    plate = choose_plate(MANIFEST.get("id") or MANIFEST["site_url"], MANIFEST.get("reel_plate"))
    REEL_VIEWPORT = reel_viewport(plate)
    site = MANIFEST["site_url"]
    if not safe_host(site):
        raise ValueError("Only public HTTPS websites can be captured")
    shots = OUT / "preflight"
    shots.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=["--no-sandbox", "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport=REEL_VIEWPORT, device_scale_factor=1, reduced_motion="no-preference")
        prepare_context(ctx)
        ctx.route("**/*", lambda route: route.continue_() if safe_host(route.request.url) else route.abort())
        page = ctx.new_page()
        open_site(page, site)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(4500)  # the same wait the Reel gives loaders and intros
        wait_for_loader(page)
        dismiss_cookies(page)
        bottom = page.evaluate("Math.max(0, document.documentElement.scrollHeight - innerHeight)")
        kind = "page" if bottom >= 300 else "wheel"
        height = REEL_VIEWPORT["height"]
        page.mouse.move(REEL_VIEWPORT["width"] / 2, height / 2)
        paths = []
        for i in range(8):
            if i:
                if kind == "page":
                    page.evaluate("(y) => window.lenis?.scrollTo ? window.lenis.scrollTo(y, { immediate: true, force: true }) : scrollTo(0, y)", bottom * i / 7)
                else:
                    for _ in range(6):  # about the Reel's wheel pace, in chunks the site can follow
                        page.mouse.wheel(0, height * WHEEL_SPEED * 2 / 6)
                        page.wait_for_timeout(150)
                page.wait_for_timeout(1500 if kind == "page" else 2500)
            path = shots / f"{i:02d}.jpg"
            try:
                page.screenshot(path=str(path), type="jpeg", quality=80, timeout=45000)
            except Exception as e:
                raise RuntimeError(f"The site is too heavy to record ({type(e).__name__} on screenshot {i + 1})")
            Image.open(path).convert("RGB").resize((768, round(768 * height / REEL_VIEWPORT["width"]))).save(path, quality=80)
            paths.append(path)
        browser.close()
    diffs = _motion(paths)
    stats = {"kind": kind, "screens": round(bottom / height, 1), "diffs": diffs, "changed": sum(d > 4 for d in diffs)}
    (OUT / "preflight.json").write_text(json.dumps(stats))
    print(f"Preflight: {stats}", flush=True)


def check_motion():
    """Belt and braces after the full capture: a Reel whose screen hardly changes isn't worth posting."""
    paths = sorted(FRAMES.glob("*.jpg"))
    sample = paths[:: max(1, len(paths) // 16)]
    diffs = _motion(sample)
    changed = sum(d > 4 for d in diffs)
    print(f"Reel motion: {changed}/{len(diffs)} steps changed {diffs}", flush=True)
    if changed < 4:
        raise RuntimeError(f"Reel barely moves ({changed}/{len(diffs)} steps changed): the site didn't scroll or got stuck")


def wheel_reel(page, step_ms):
    """Capture a site that scrolls on wheel events rather than page height.

    These are usually WebGL sites, which render in software on a GPU-less
    runner at about two seconds a frame. So: capture at 30 fps and write each
    capture twice (the reel is 60 fps; on a phone it looks the same), cap at
    16 seconds, hold at both ends, WHEEL_SPEED screens of wheel per second, and stop early once the screen has been still for 3 seconds.
    """
    import shutil
    width, height = REEL_VIEWPORT["width"], REEL_VIEWPORT["height"]
    page.mouse.move(width / 2, height / 2)
    rate = 30
    per_capture = height * WHEEL_SPEED / rate
    hold_start, hold_end = int(HOLD_START * rate), int(HOLD_END * rate)
    total = int(min(MAX_SECONDS, 16.0) * rate)
    page.evaluate("window.__vclock.start()")
    still, last, n = 0, None, 0
    for i in range(total):
        if hold_start <= i < total - hold_end:
            page.mouse.wheel(0, per_capture)
        page.evaluate("async (ms) => { await window.__vclock.step(ms); await window.__vclock.settle(); }", 1000 / rate)
        if i % 10 == 0:
            dismiss_cookies_now(page)
        frame = FRAMES / f"{n:05d}.jpg"
        shot(page, frame, FRAMES / f"{n - 1:05d}.jpg" if n else None, type="jpeg", quality=92, animations="allow", caret="hide")
        shutil.copyfile(frame, FRAMES / f"{n + 1:05d}.jpg")
        n += 2
        size = frame.stat().st_size
        still = still + 1 if last is not None and abs(size - last) < 200 else 0
        last = size
        if i > hold_start + 3 * rate and still > 3 * rate:
            break
        if i % 60 == 0:
            print(f"Captured {i}/{total} wheel frames", flush=True)
    if not n:
        raise RuntimeError("The site produced no frames")

def data_uri(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def render_case_study(case):
    """Render the case-study tile (1080 × 1350) from the job's copy and design picks.

    The copy is written and fact-checked by the dashboard when the job is queued;
    this only lays it out, using the client's own desktop and phone screenshots.
    """
    template = (Path(__file__).parent / "case-study" / "template.html").read_text()
    payload = dict(case, images={"desktop": data_uri(OUT / "desktop.png"), "mobile": data_uri(OUT / "mobile.png")})
    payload.setdefault("design", {}).setdefault("accent", MANIFEST["accent_color"])
    html = template.replace("<script>\n(() => {", "<script>window.CASE = " + json.dumps(payload).replace("</", "<\\/") + ";</script>\n<script>\n(() => {", 1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome", args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        page.set_content(html, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("body[data-ready='1']", timeout=30000)
        page.locator("#post").screenshot(path=str(OUT / "case-study.png"))
        browser.close()
    Image.open(OUT / "case-study.png").convert("RGB").save(OUT / "case-study.jpg", quality=93, subsampling=0)


def screenshot_accent(path):
    """The site's most common strong colour in its desktop screenshot, or None
    for a monochrome site. Used to light the room when no brand colour is set."""
    im = Image.open(path).convert("RGB").resize((240, 150))
    best, counts = None, {}
    for r, g, b in im.getdata():
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s < 0.35 or l < 0.18 or l > 0.85:
            continue
        key = int(h * 24)
        c = counts.setdefault(key, [0, 0, 0, 0])
        c[0] += 1; c[1] += r; c[2] += g; c[3] += b
    total = 240 * 150
    if counts:
        key, c = max(counts.items(), key=lambda kv: kv[1][0])
        if c[0] / total >= 0.004:
            best = "#" + "".join(f"{round(v / c[0]):02x}" for v in c[1:])
    return best


def pick(seed, items):
    """A stable pseudo-random choice per job (different salt per use)."""
    import hashlib
    return items[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(items)] if items else None


_COMPOSITOR = None


def _init_compositor(plate, size, light, hook):
    global _COMPOSITOR
    sys.path.insert(0, str(Path(__file__).parent / "macbook"))
    from composite import MacbookCompositor
    _COMPOSITOR = MacbookCompositor(plate, size, out_size=MASTER, light=light, hook=hook)


def _composite_frame(job):
    path, t, seconds = job
    with Image.open(path) as frame:
        return _COMPOSITOR.frame(frame.convert("RGB"), t, seconds)


def render_reel():
    sys.path.insert(0, str(Path(__file__).parent / "macbook"))
    from composite import MacbookCompositor, choose_plate

    paths = sorted(FRAMES.glob("*.jpg"))
    frames = len(paths)
    seed = MANIFEST.get("id") or MANIFEST["site_url"]
    plate = choose_plate(seed, MANIFEST.get("reel_plate"))
    hook = (MANIFEST.get("reel_hook") or "").strip() or pick(seed + ":hook", json.loads((Path(__file__).parent / "macbook" / "hooks.json").read_text())["hooks"])
    track = MANIFEST.get("reel_music") or None
    music_file = None
    if track and track.get("url"):
        from urllib.request import urlopen
        music_file = OUT / "music-track"
        with urlopen(track["url"], timeout=60) as res:
            music_file.write_bytes(res.read())
    music = track["name"] if music_file else None
    # Room light: the brand colour; if none was set or detected, the site's
    # own strongest colour; if it's monochrome, the plate's original amber.
    light = MANIFEST["accent_color"] if MANIFEST["accent_color"].lower() != "#315cff" or "bluepeek" in MANIFEST["site_url"] else None
    print(f"Compositing onto the {plate['id']} MacBook plate, lit {light or 'original amber'}, track {music or 'none (silent)'}", flush=True)
    seconds = frames / FPS
    mw, mh = MASTER
    audio = (f"[1:a]atrim=0:{seconds:.3f},afade=t=in:d=0.4,afade=t=out:st={max(0, seconds - 1.8):.3f}:d=1.8,loudnorm=I=-14:TP=-1.5:LRA=11,asplit[a1][a2]"
             if music_file else "[1:a]asplit[a1][a2]")
    video_opts = lambda crf: [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-profile:v", "high",
        "-g", str(FPS // 2), "-bf", "2", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-r", str(FPS), "-t", f"{seconds:.3f}",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
    ]
    command = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{mw}x{mh}", "-framerate", str(FPS), "-i", "-",
        *(["-ss", str(float(track.get("start_seconds") or 0)), "-i", str(music_file)] if music_file
          else ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]),
        # A whisper of temporal grain (was 5: Zach wants it "super clean") so the
        # plate still breathes like footage. Then the 1080p copy, Lanczos-scaled
        # from the 4K master: sharper after Instagram's own re-encode.
        "-filter_complex", f"[0:v]{GRADE},noise=c0s=2:c0f=t+u,split[m][d];[d]scale=1080:1920:flags=lanczos[s];{audio}",
        "-map", "[m]", "-map", "[a1]", *video_opts("16"), str(OUT / "reel-4k.mp4"),
        "-map", "[s]", "-map", "[a2]", *video_opts("15"), str(OUT / "reel.mp4"),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
    # Composite on every core (GitHub runners have several; one core took
    # ~10 min of a ~15 min render). Each worker builds its own compositor;
    # imap keeps the frames in order for the encoder.
    import multiprocessing as mp
    workers = max(1, int(os.environ.get("SHOWCASE_WORKERS") or os.cpu_count() or 1))
    print(f"Compositing with {workers} workers", flush=True)
    jobs = [(str(path), i / max(1, frames - 1), i / FPS) for i, path in enumerate(paths)]
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context()
    with ctx.Pool(workers, initializer=_init_compositor, initargs=(plate, Image.open(paths[0]).size, light, hook)) as pool:
        for i, data in enumerate(pool.imap(_composite_frame, jobs, chunksize=6)):
            encoder.stdin.write(data)
            if i % 240 == 0:
                print(f"Composited {i}/{frames} frames", flush=True)
    encoder.stdin.close()
    if encoder.wait(timeout=900) != 0:
        raise RuntimeError("ffmpeg failed while encoding the Reel")
    (OUT / "reel-plate.txt").write_text(plate["id"])
    (OUT / "reel-meta.json").write_text(json.dumps({"plate": plate["id"], "hook": hook, "music": music, "light": light}))


if __name__ == "__main__":
    if os.environ.get("SHOWCASE_PREFLIGHT") == "1":
        preflight()
        sys.exit(0)
    capture()
    check_motion()
    # No brand colour set or detected: use the site's own strongest colour for
    # every tile (mockup backdrop, case study accent, room light), so a gold
    # site doesn't get Bluepeek's default blue. Monochrome sites keep it.
    if MANIFEST["accent_color"].lower() == "#315cff" and "bluepeek" not in MANIFEST["site_url"]:
        MANIFEST["accent_color"] = screenshot_accent(OUT / "desktop.png") or MANIFEST["accent_color"]
    (OUT / "metadata.json").write_text(json.dumps({"accent_color": MANIFEST["accent_color"]}))
    colour = rgb(MANIFEST["accent_color"])
    make_mockup(colour)
    if MANIFEST.get("case_study"):
        render_case_study(MANIFEST["case_study"])
    render_reel()
    print("Showcase complete", flush=True)
