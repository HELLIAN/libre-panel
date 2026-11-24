"""Render the images in docs/images from the built-in themes.

    python tools/readme_images.py [docs/images] [--only NAME ...] [--media docs/media]

Each theme as the panel shows it (<theme>.png), the README's hero, gallery,
"fits your machine" and module pictures, and the 1280x640 social preview
(upload it in the repository's settings). With Playwright installed, also
the screenshots of the app. Demo sensor values and a fixed time keep the pictures stable.
Libre Panel's own themes lead: Lumen in the hero, the gallery and the social preview.
The picture of three panels at once is the app's own *Picture of all panels*, of virtual panels;
the stills of the feature grid (tile-*.png) are made of the pictures here and of the clips'
posters in docs/media, so tools/readme_media.py runs first. :func:`pictures` names every file
it writes; ``--only`` makes some of them.

Libre Panel itself draws them: lp-check and the program, built in rust/ (tools/lp_check.py), the
app's pictures on a computer it is told of (no one's own: LP_HARDWARE, LP_DEMO_READINGS, LP_NOW).
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import functools
import io
import json
import math
import os
import re
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# this checkout's Libre Panel draws them: lp-check, built in rust/ (tools/lp_check.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lp_check import ask, libraries, run, typed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WHEN = datetime(2026, 10, 9, 10, 8, 24).timestamp()  # a Friday morning
BG_TOP, BG_BOTTOM = (13, 20, 29), (6, 9, 13)
TX_1, TX_3, ACCENT = (238, 246, 255), (140, 160, 184), (19, 229, 215)

# Each theme as the panel shows it: Libre Panel's own first, the bars before the small panels.
THEMES = ("lumen", "lumen-companion", "studio", "encore", "nimbus", "almanac", "almanac-analog",
          "terra", "arena", "agents", "contrast", "libre-default", "slate", "orbit", "column",
          "pico")  # fmt: skip
# The pictures made of them, and the screenshots of the app (those need Playwright).
COMPOSED = ("hero.png", "gallery.png", "social-preview.png", "studio-adapts.png", "vertical.png",
            "module-sizes.png", "weather-sizes.png", "looks.png", "module-layouts.png",
            "module-catalog.png", "module-layouts-more.png", "more-panels.png",
            "panels-at-once.png", "round-screens.png", "zc360-screens.png")  # fmt: skip
APP_SHOTS = ("editor-new.png", "editor.png", "editor-drag.png", "palette.png", "start.png",
             "settings.png")  # fmt: skip
# The README's own parts: the website's download buttons in each language of the README (they
# need Playwright), and the icons of its overview, drawn from the app's own (ui/icons.js).
BUTTONS = {
    "button-windows.png": ("en", "setup"), "button-linux.png": ("en", "appimage"),
    "button-windows-de.png": ("de", "setup"), "button-linux-de.png": ("de", "appimage"),
}  # fmt: skip
ICONS = {f"icon-{name}.svg": name for name in ("pulse", "layers", "palette", "blocks", "check",
                                                "lock")}  # fmt: skip


def pictures() -> list[str]:
    """Every file this script writes into its folder, by name: what the README and the
    website may show from docs/images."""
    return (
        [f"{theme_id}.png" for theme_id in THEMES] + list(COMPOSED) + list(APP_SHOTS) + list(TILES)
        + list(BUTTONS) + list(ICONS)
    )  # fmt: skip


# What the pictures show over the demo's readings at :data:`WHEN`: (value, unit, label).
EXTRA = {
    # the weather of a mild morning
    "weather.temperature": (17.0, "°C", "Temperature"),
    "weather.apparent_temperature": (16.0, "°C", "Feels like"),
    "weather.humidity": (58.0, "%", "Humidity"), "weather.wind_speed": (12.0, "km/h", "Wind"),
    "weather.code": (2.0, "", "Weather code"),
    "weather.description": ("Partly cloudy", "", "Weather"),
    # a moon worth looking at (the day of these pictures has a new one)
    "moon.phase": (0.36, "", "Moon phase"), "moon.illumination": (79.0, "%", "Moon lit"),
    "moon.name": ("Waxing gibbous", "", "Moon"), "moon.full_in": (4.1, "d", "Full moon in"),
    "moon.new_in": (18.9, "d", "New moon in"),
    # the newer modules' sources: the ping, a UPS, the microphone (muted, to show it)
    "net.ping": (14.0, "ms", "Ping"), "net.ping.lost": (0.0, "%", "Lost"),
    "net.ping.host": ("1.1.1.1", "", "Host"), "ups.charge": (100.0, "%", "UPS charge"),
    "ups.runtime": (38.0, "min", "UPS runtime"), "ups.load": (21.0, "%", "UPS load"),
    "ups.power": (126.0, "W", "UPS power"), "ups.status": ("Online", "", "UPS state"),
    "audio.mic.muted": (1, "", "Microphone muted"),
}  # fmt: skip


def snapshot(drop: tuple[str, ...] = ()) -> dict:
    """The demo's snapshot at :data:`WHEN` with :data:`EXTRA` over it, as lp-check takes it;
    the readings of ``drop`` left out."""
    readings = {key: {"v": typed(value), "unit": unit, "label": label}
                for key, (value, unit, label) in EXTRA.items()}  # fmt: skip
    return {"demo": WHEN, "readings": readings, "drop": list(drop)}


@functools.cache
def reading_keys() -> tuple[str, ...]:
    """The keys of the readings the pictures show: the demo's at :data:`WHEN`, and EXTRA's."""
    (said,) = ask([{"op": "demo_keys", "t": WHEN}])
    return tuple(dict.fromkeys([*said["keys"], *EXTRA]))


def theme_folder(theme_id: str) -> Path:
    return ROOT / "data" / "themes" / theme_id


def theme_name(theme_id: str) -> str:
    """A built-in theme's name, as its theme.json says it."""
    data = json.loads((theme_folder(theme_id) / "theme.json").read_text(encoding="utf-8"))
    return data.get("name") or theme_id


def drawn(frames: list[dict]) -> list[dict]:
    """Each frame as Libre Panel draws it (lp-check's render_frame, an editor's preview), with
    its picture (RGB) and its modules' boxes."""
    case = {"op": "render_frame", "fonts_dir": str(ROOT / "data" / "fonts"),
            "locale": str(ROOT / "data" / "locale"),
            "frames": [{"now": 0.0, "bytes": True, "module_boxes": True, **frame}
                       for frame in frames]}  # fmt: skip
    out = []
    for frame, done in zip(frames, json.loads(run(json.dumps(case) + "\n"))["frames"],
                           strict=True):  # fmt: skip
        if "frame" not in done:
            raise RuntimeError(f"{frame.get('folder')}: not drawn: {done.get('error') or done}")
        image = done["frame"]
        picture = Image.frombytes(image["mode"], tuple(image["size"]),
                                  base64.b64decode(image["data"]))  # fmt: skip
        out.append({**done, "picture": picture.convert("RGB")})
    return out


def render(theme_id: str, factor: int = 1, drop: tuple[str, ...] = ()) -> Image.Image:
    """A built-in theme as the panel shows it (``factor``: scaled up first, as the app adapts
    a theme to a larger panel), on the demo's readings at :data:`WHEN`."""
    frame = {"folder": str(theme_folder(theme_id)), "snapshot": snapshot(drop)}
    if factor != 1:
        frame["adapt"] = {"factor": factor}
    return drawn([frame])[0]["picture"]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """One of Libre Panel's own fonts (data/fonts), by its name."""
    (path,) = sorted((ROOT / "data" / "fonts").glob(f"{name}.[ot]tf"))
    return ImageFont.truetype(str(path), size)


def logo(size: int) -> Image.Image:
    """The app's icon as Libre Panel draws it (rust/render/src/branding.rs)."""
    (said,) = ask([{"op": "logo", "size": size}])
    image = said["image"]
    return Image.frombytes(image["mode"], tuple(image["size"]), base64.b64decode(image["data"]))


@functools.cache
def parts(layouts: tuple[tuple[str, str], ...] = ()) -> dict:
    """What the module pictures are made of (lp-check's module_parts): the looks (key, name,
    palette, cards), the kinds' names, each [model, orientation]'s size, grid and templates,
    the models' inches and pixels, and Lumen's pair version."""
    (said,) = ask([{"op": "module_parts", "layouts": [list(each) for each in layouts],
                    "pairs": ["lumen"], "builtin": str(ROOT / "data" / "themes"),
                    "user": str(ROOT / "no-such-folder")}])  # fmt: skip
    said["looks"] = {key: {"name": json.loads(name), "palette": json.loads(palette),
                           "style": json.loads(style)}
                     for key, name, palette, style in said["looks"]}  # fmt: skip
    for layout in said["layouts"]:
        layout["templates"] = json.loads(layout["templates"])
    return said


def background(w: int, h: int, glow: tuple[float, float] | None = (0.5, 0.0)) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        f = y / max(1, h - 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(round(a + (b - a) * f) for a, b in zip(BG_TOP, BG_BOTTOM, strict=True)),
        )
    if glow:
        halo = Image.new("L", (w, h), 0)
        gx, gy = glow
        ImageDraw.Draw(halo).ellipse(
            [gx * w - w * 0.35, gy * h - h * 0.5, gx * w + w * 0.35, gy * h + h * 0.5], fill=46
        )
        halo = halo.filter(ImageFilter.GaussianBlur(min(w, h) * 0.2))
        img = Image.composite(Image.new("RGB", (w, h), ACCENT), img, halo)
    return img


def device(screen: Image.Image, border: int, round_panel: bool = False) -> Image.Image:
    """The screen in a dark bezel with a soft shadow (RGBA, shadow margin included)."""
    sw, sh = screen.size
    pad = border * 4  # room for the shadow
    w, h = sw + 2 * border + 2 * pad, sh + 2 * border + 2 * pad
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body = [pad, pad, w - pad - 1, h - pad - 1]
    radius = border * 2

    shadow = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shadow)
    shifted = [body[0], body[1] + border, body[2], body[3] + border]
    if round_panel:
        sd.ellipse(shifted, fill=200)
    else:
        sd.rounded_rectangle(shifted, radius, fill=200)
    shadow = shadow.filter(ImageFilter.GaussianBlur(border * 1.6))
    out.paste(Image.new("RGBA", (w, h), (0, 0, 0, 255)), (0, 0), shadow)

    d = ImageDraw.Draw(out)
    edge, fill = (52, 64, 80, 255), (11, 14, 19, 255)
    if round_panel:
        d.ellipse(body, fill=edge)
        d.ellipse([body[0] + 2, body[1] + 2, body[2] - 2, body[3] - 2], fill=fill)
    else:
        d.rounded_rectangle(body, radius, fill=edge)
        d.rounded_rectangle(
            [body[0] + 2, body[1] + 2, body[2] - 2, body[3] - 2], radius - 2, fill=fill
        )

    mask = Image.new("L", screen.size, 0)
    md = ImageDraw.Draw(mask)
    if round_panel:
        md.ellipse([0, 0, sw - 1, sh - 1], fill=255)
    else:
        md.rounded_rectangle([0, 0, sw - 1, sh - 1], max(2, border // 3), fill=255)
    out.paste(screen, (pad + border, pad + border), mask)

    # a faint reflection across the glass
    sheen = Image.new("L", screen.size, 0)
    sd = ImageDraw.Draw(sheen)
    for x in range(sw):
        sd.line([(x, 0), (x, sh)], fill=max(0, round(7 * (1 - x / (0.55 * sw)))))
    sheen = Image.composite(sheen, Image.new("L", screen.size, 0), mask)
    out.paste(
        Image.new("RGBA", screen.size, (255, 255, 255, 255)), (pad + border, pad + border), sheen
    )
    return out


def shadow_margin(border: int) -> int:
    return border * 4


def text_center(draw, cx, y, text, fnt, fill):
    w = draw.textlength(text, font=fnt)
    draw.text((cx - w / 2, y), text, font=fnt, fill=fill)


def hero(bar2x: Image.Image) -> Image.Image:
    """Lumen, Libre Panel's signature screen, on its 9.2" bar."""
    screen = bar2x.resize((1920, 480), Image.LANCZOS)
    border = 18
    dev = device(screen, border)
    m = shadow_margin(border)
    w = 2000
    h = dev.height - 2 * m + 2 * 56
    img = background(w, h, glow=(0.5, 1.1)).convert("RGBA")
    img.alpha_composite(dev, ((w - dev.width) // 2, 56 - m))
    return img.convert("RGB")


def gallery(shots: dict[str, Image.Image]) -> Image.Image:
    """Libre Panel's own themes: Lumen and Studio across the width, the smaller panels below."""
    w, margin, gap, border = 2000, 44, 44, 12
    label, sub = font("Barlow-SemiBold", 30), font("Barlow-Regular", 24)
    m = shadow_margin(border)

    # rows 1 and 2: the bars across the width, Lumen first
    bar_w = w - 2 * margin - 2 * border
    bars = [
        (shots[theme_id].resize((bar_w, bar_w // 4), Image.LANCZOS), name)
        for theme_id, name in (("lumen", "Lumen"), ("studio", "Studio"))
    ]
    # row 3: the smaller panels at one screen height (Pico larger than life)
    hh = 270
    row = [
        ("slate", "Slate", '5" · 800×480'),
        ("libre-default", "Libre Default", '3.5" · 480×320'),
        ("orbit", "Orbit", '2.1" / 2.8" round'),
        ("column", "Column", '3.5" portrait'),
        ("pico", "Pico", '0.96" · 160×80'),
    ]
    screens = []
    for theme_id, name, size in row:
        src = shots[theme_id]
        if theme_id == "pico":
            scr = src.resize((320, 160), Image.LANCZOS)
        else:
            scr = src.resize((round(src.width * hh / src.height), hh), Image.LANCZOS)
        screens.append((scr, name, size, theme_id == "orbit"))
    total = sum(s.width + 2 * border for s, *_ in screens)
    spacing = (w - 2 * margin - total) / (len(screens) - 1)

    caption_h = 92
    bar_h = bars[0][0].height + 2 * border
    row2_h = hh + 2 * border
    h = margin + len(bars) * (bar_h + caption_h + gap // 2) + row2_h + caption_h + margin // 2
    img = background(w, h, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)

    y = margin
    for bar, name in bars:
        img.alpha_composite(device(bar, border), (margin - m, y - m))
        cy = y + bar_h + 22
        text_center(draw, w / 2, cy, name, label, TX_1)
        text_center(draw, w / 2, cy + 38, '8.8" / 9.2" bar · 1920×480', sub, TX_3)
        y += bar_h + caption_h + gap // 2

    x = margin
    for scr, name, size, is_round in screens:
        dev = device(scr, border, round_panel=is_round)
        top = y + (row2_h - (scr.height + 2 * border)) // 2
        img.alpha_composite(dev, (round(x) - m, top - m))
        cx = x + (scr.width + 2 * border) / 2
        text_center(draw, cx, y + row2_h + 22, name, label, TX_1)
        text_center(draw, cx, y + row2_h + 60, size, sub, TX_3)
        x += scr.width + 2 * border + spacing
    return img.convert("RGB")


VERTICAL = ("lumen", "studio", "encore", "nimbus", "almanac", "almanac-analog", "arena",
            "round-report")  # fmt: skip


def vertical() -> Image.Image:
    """The screens of the bars in their versions for a bar on end, side by side."""
    border = 14
    shots = [device(render(f"{name}-vertical"), border) for name in VERTICAL]
    pad = shadow_margin(border)
    gap, margin = 36 - 2 * pad, 64 - pad
    w = sum(shot.width for shot in shots) + gap * (len(shots) - 1) + 2 * margin
    h = shots[0].height + 2 * margin + 54
    img = background(w, h, glow=(0.5, 1.0))
    draw = ImageDraw.Draw(img)
    x = margin
    for name, shot in zip(VERTICAL, shots, strict=True):
        img.paste(shot, (x, margin), shot)
        title = theme_name(f"{name}-vertical").removesuffix(" Vertical")
        text_center(draw, x + shot.width / 2, margin + shot.height - pad + 22, title,
                    font("Barlow-SemiBold", 30), (226, 232, 240))  # fmt: skip
        x += shot.width + gap
    # (eight panels in true pixels are 4400 px across: more than a page shows)
    return img.resize((round(w * 0.6), round(h * 0.6)), Image.Resampling.LANCZOS)


ROUND = ("lumen", "encore", "nimbus", "almanac", "arena", "round-report")
ZC360 = ("lumen-cpu", "lumen-gpu", "lumen-memory")  # (as the three screens start)


def round_screens() -> Image.Image:
    """The screens in their versions for the round panels of 480x480, three by two."""
    border, cols, title = 14, 3, 58
    shots = [device(render(f"{name}-480x480"), border, round_panel=True) for name in ROUND]
    pad = shadow_margin(border)
    gap, margin = 44 - 2 * pad, 64 - pad
    cw, ch = shots[0].size
    rows = -(-len(shots) // cols)
    w = cols * cw + (cols - 1) * gap + 2 * margin
    h = rows * (ch + title) + (rows - 1) * gap + 2 * margin
    img = background(w, h, glow=(0.5, 0.0))
    draw = ImageDraw.Draw(img)
    for i, (name, shot) in enumerate(zip(ROUND, shots, strict=True)):
        x = margin + (i % cols) * (cw + gap)
        y = margin + (i // cols) * (ch + title + gap)
        img.paste(shot, (x, y), shot)
        label = theme_name(f"{name}-480x480")
        text_center(draw, x + cw / 2, y + ch - pad + 26, label, font("Barlow-SemiBold", 30),
                    (226, 232, 240))  # fmt: skip
    return img.resize((round(w * 0.6), round(h * 0.6)), Image.Resampling.LANCZOS)


def zc360_screens() -> Image.Image:
    """The ZC-360's three screens, each a panel of its own, one above the other: the Lumen each
    starts with."""
    border, title = 20, 60
    shots = [device(render(f"{name}-640x180", 2), border) for name in ZC360]
    pad = shadow_margin(border)
    gap, margin = 30 - 2 * pad, 64 - pad
    cw, ch = shots[0].size
    w = cw + 2 * margin
    h = len(shots) * (ch + title) + (len(shots) - 1) * gap + 2 * margin
    img = background(w, h, glow=(0.5, 0.0))
    draw = ImageDraw.Draw(img)
    for i, (name, shot) in enumerate(zip(ZC360, shots, strict=True)):
        y = margin + i * (ch + title + gap)
        img.paste(shot, (margin, y), shot)
        label = f"{i + 1} · {theme_name(f'{name}-640x180')}"
        text_center(draw, w / 2, y + ch - pad + 28, label, font("Barlow-SemiBold", 34),
                    (226, 232, 240))  # fmt: skip
    return img.resize((round(w * 0.6), round(h * 0.6)), Image.Resampling.LANCZOS)


def social(bar2x: Image.Image) -> Image.Image:
    """1280x640 for the repository's social preview: Lumen under the name and the promise."""
    w, h = 1280, 640
    img = background(w, h, glow=(0.5, 1.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    title, tag = font("Barlow-Bold", 96), font("Barlow-Medium", 38)
    small = font("Barlow-Regular", 28)
    img.alpha_composite(logo(112), (68, 40))  # the app's icon, then its name
    draw.text((204, 44), "Libre Panel", font=title, fill=TX_1)
    draw.rounded_rectangle([208, 158, 208 + 120, 158 + 7], 3, fill=ACCENT)
    draw.text((72, 188), "Free software for USB system-monitor displays", font=tag, fill=TX_1)
    draw.text(
        (72, 240),
        "Video mode up to 50 fps · a design app · every panel size · Windows and Linux",
        font=small,
        fill=TX_3,
    )
    border = 10
    screen = bar2x.resize((1120, 280), Image.LANCZOS)
    dev = device(screen, border)
    m = shadow_margin(border)
    img.alpha_composite(dev, ((w - dev.width) // 2, 304 - m))
    return img.convert("RGB")


def adapts(variants: list[tuple[Image.Image, str, str]]) -> Image.Image:
    """Studio where nothing plays and the weather is off, and on a machine without a GPU
    sensor."""
    w, margin, border = 1800, 48, 14
    label, sub = font("Barlow-SemiBold", 34), font("Barlow-Regular", 28)
    m = shadow_margin(border)
    bar_w = w - 2 * margin - 2 * border
    bar_h = bar_w // 4
    block = 64 + bar_h + 2 * border + 40
    img = background(w, margin + block * len(variants), glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    y = margin
    for screen, title, note in variants:
        draw.text((margin + 4, y), title, font=label, fill=TX_1)
        draw.text(
            (margin + 12 + draw.textlength(title, font=label), y + 5), note, font=sub, fill=TX_3
        )
        dev = device(screen.resize((bar_w, bar_h), Image.LANCZOS), border)
        img.alpha_composite(dev, (margin - m, y + 56 - m))
        y += block
    return img.convert("RGB")


def module_frame(model: str, look: str, modules: list[dict], orientation: str = "landscape"):
    """A theme made of modules, rendered; and its module boxes."""
    chosen = parts()["looks"][look]
    palette, style = chosen["palette"], chosen["style"]
    theme = {
        "format": "libre-panel-theme/1",
        "display": {"model": model, "orientation": orientation},
        "palette": palette,
        "style": style,
        "background": {"color": palette["bg"]},
        "widgets": [{"type": "module", "id": f"m{i}", **m} for i, m in enumerate(modules)],
    }
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder) / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
        (done,) = drawn([{"folder": folder, "snapshot": snapshot()}])
    return done["picture"], {key: tuple(box) for key, box in done["module_boxes"].items()}


def template(model: str, which: str, orientation: str = "landscape") -> list[dict]:
    """The modules of a starting layout (``which``) for ``model``'s grid."""
    (layout,) = parts(((model, orientation),))["layouts"]
    chosen = next(t for t in layout["templates"] if t["id"] == which)
    return chosen["modules"]


def sizes_picture(kind: str, options: dict, rows: list) -> Image.Image:
    """One module at several sizes, in rows: [[((cols, rows), size, note), ...], ...]."""
    shots = []
    for row in rows:
        line = []
        for (cols, rows_), size, note in row:
            module = {"module": kind, "cols": cols, "rows": rows_, **options}
            frame, boxes = module_frame("turing-9.2-usb", "arctic", [module])
            x, y, w, h = boxes["m0"]
            line.append((frame.crop((x - 12, y - 12, x + w + 12, y + h + 12)), size, note))
        shots.append(line)
    label, sub = font("Barlow-SemiBold", 30), font("Barlow-Regular", 24)
    margin, gap, head = 44, 36, 70
    width = (
        max(sum(c.width for c, *_ in line) + gap * (len(line) - 1) for line in shots) + 2 * margin
    )
    height = margin + sum(max(c.height for c, *_ in line) + head for line in shots)
    height += gap * (len(shots) - 1) + margin // 2
    img = background(width, height, glow=(0.5, 0.0))
    draw = ImageDraw.Draw(img)
    y = margin
    for line in shots:
        x = margin
        for crop, size, note in line:
            draw.text((x + 12, y), size, font=label, fill=TX_1)
            draw.text(
                (x + 12 + draw.textlength(size + "  ", font=label), y + 5),
                note,
                font=sub,
                fill=TX_3,
            )
            img.paste(crop, (x, y + head - 12))
            x += crop.width + gap
        y += max(c.height for c, *_ in line) + head + gap
    return img


def module_sizes() -> Image.Image:
    """One CPU ring module at five sizes: the larger, the more it shows."""
    return sizes_picture(
        "ring",
        {"source": "cpu"},
        [
            [((1, 1), "1 × 1", "the load"), ((2, 1), "2 × 1", "+ name, temperature, power"),
             ((3, 1), "3 × 1", "+ history")],
            [((2, 2), "2 × 2", "big: details over history"),
             ((4, 2), "4 × 2", "everything, large")],
        ],
    )  # fmt: skip


def weather_sizes() -> Image.Image:
    """The weather module at five sizes: the forecast comes with room."""
    return sizes_picture(
        "weather",
        {},
        [
            [((1, 1), "1 × 1", "now"), ((3, 1), "3 × 1", "+ the days ahead")],
            [((1, 2), "1 × 2", "+ days as rows"), ((2, 2), "2 × 2", "now over the days"),
             ((4, 2), "4 × 2", "days and hours")],
        ],
    )  # fmt: skip


def looks() -> Image.Image:
    """The same layout in every look."""
    modules = template("turing-9.2-usb", "overview")
    shots = [
        (look["name"], module_frame("turing-9.2-usb", key, modules)[0])
        for key, look in parts()["looks"].items()
    ]
    border, margin, gap, caption = 10, 44, 34, 52
    label = font("Barlow-SemiBold", 30)
    cell_w = 940
    cell_h = cell_w // 4
    m = shadow_margin(border)
    width = 2 * margin + 2 * (cell_w + 2 * border) + gap
    rows = -(-len(shots) // 2)  # two side by side, as many rows as it takes
    height = margin + rows * (cell_h + 2 * border + caption) + (rows - 1) * gap // 2 + margin // 2
    img = background(width, height, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    for i, (name, frame) in enumerate(shots):
        col, row = i % 2, i // 2
        x = margin + col * (cell_w + 2 * border + gap)
        y = margin + row * (cell_h + 2 * border + caption + gap // 2)
        dev = device(frame.resize((cell_w, cell_h), Image.LANCZOS), border)
        img.alpha_composite(dev, (x - m, y - m))
        draw.text((x + 4, y + cell_h + 2 * border + 10), name, font=label, fill=TX_1)
    return img.convert("RGB")


def module_layouts() -> Image.Image:
    """Starting layouts on several panels, each in another look."""
    bars = [
        (
            "Performance · Neon",
            module_frame("turing-9.2-usb", "neon", template("turing-9.2-usb", "performance"))[0],
        ),
        (
            "Calm · Sunset",
            module_frame("turing-9.2-usb", "sunset", template("turing-9.2-usb", "calm"))[0],
        ),
    ]
    specs = [
        ('3.5" · Paper', "turing-3.5", "paper", "overview", "landscape"),
        ('5" · Graphite', "turing-5", "graphite", "performance", "landscape"),
        ('3.5" portrait · Mono', "turing-3.5", "mono", "overview", "portrait"),
        ('2.1" round · Arctic', "turing-2.1", "arctic", "performance", "landscape"),
    ]
    small = [
        (
            name,
            module_frame(model, look, template(model, which, side), side)[0],
            model == "turing-2.1",
        )
        for name, model, look, which, side in specs
    ]
    border, margin, gap, caption = 12, 44, 40, 56
    label = font("Barlow-SemiBold", 28)
    m = shadow_margin(border)
    bar_w = 940
    hh = 300
    sized = []
    for name, frame, is_round in small:
        sized.append(
            (
                name,
                frame.resize((round(frame.width * hh / frame.height), hh), Image.LANCZOS),
                is_round,
            )
        )
    width = 2 * margin + 2 * (bar_w + 2 * border) + gap
    row1 = bar_w // 4 + 2 * border
    height = margin + row1 + caption + gap // 2 + hh + 2 * border + caption + margin // 2
    img = background(width, height, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    for i, (name, frame) in enumerate(bars):
        x = margin + i * (bar_w + 2 * border + gap)
        dev = device(frame.resize((bar_w, bar_w // 4), Image.LANCZOS), border)
        img.alpha_composite(dev, (x - m, margin - m))
        draw.text((x + 4, margin + row1 + 10), name, font=label, fill=TX_1)
    total = sum(f.width + 2 * border for _, f, _ in sized)
    spacing = (width - 2 * margin - total) / (len(sized) - 1)
    x = margin
    y = margin + row1 + caption + gap // 2
    for name, frame, is_round in sized:
        dev = device(frame, border, round_panel=is_round)
        img.alpha_composite(dev, (round(x) - m, y - m))
        # (a caption wider than its panel stays inside the picture)
        tx = min(x + 4, width - margin - draw.textlength(name, font=label))
        draw.text((tx, y + hh + 2 * border + 10), name, font=label, fill=TX_1)
        x += frame.width + 2 * border + spacing
    return img.convert("RGB")


CATALOG = [  # (section, [(kind, options, cols, rows)])
    ("Hardware", [
        ("ring", {"source": "cpu"}, 2, 1), ("stat", {"source": "gpu"}, 2, 1),
        ("graph", {"source": "net"}, 2, 1), ("bars", {}, 2, 1),
        ("temps", {}, 2, 1), ("cores", {}, 2, 1), ("drives", {}, 2, 1),
        ("processes", {}, 2, 1), ("network", {}, 2, 1), ("netinfo", {}, 2, 1),
        ("ping", {}, 2, 1), ("battery", {}, 2, 1), ("power", {}, 2, 1), ("ups", {}, 2, 1),
        ("game", {}, 2, 1), ("huge", {"source": "cpu"}, 2, 1),
    ]),
    ("Time and the sky", [
        ("clock", {}, 2, 1), ("date", {}, 2, 1), ("analog", {}, 2, 1),
        ("world", {}, 2, 1), ("countdown", {"target": "12-24", "title": "Christmas"}, 2, 1),
        ("timer", {}, 2, 1), ("sun", {}, 2, 1), ("moon", {}, 2, 1), ("weather", {}, 2, 1),
        ("outlook", {}, 2, 1),
    ]),
    ("Your things", [
        ("music", {}, 2, 1), ("volume", {}, 2, 1), ("mic", {}, 2, 1), ("agenda", {}, 2, 1),
        ("values", {}, 2, 1), ("ai", {}, 2, 1), ("image", {}, 2, 1), ("system", {}, 2, 1),
        ("text", {"text": "Libre Panel"}, 2, 1),
    ]),
]  # fmt: skip


def module_catalog() -> Image.Image:
    """Every module, at two cells of a 9.2\" bar, named, in groups."""
    names = parts()["kinds"]
    columns, gap, margin, head, caption = 4, 28, 44, 64, 44
    tiles = []
    for section, modules in CATALOG:
        row = []
        for kind, options, cols, rows in modules:
            module = {"module": kind, "cols": cols, "rows": rows, **options}
            frame, boxes = module_frame("turing-9.2-usb", "arctic", [module])
            x, y, w, h = boxes["m0"]
            row.append((names[kind], frame.crop((x - 10, y - 10, x + w + 10, y + h + 10))))
        tiles.append((section, row))
    tile_w, tile_h = tiles[0][1][0][1].size
    height = margin
    for _section, row in tiles:
        lines = -(-len(row) // columns)
        height += head + lines * (tile_h + caption) + (lines - 1) * gap // 2 + gap
    width = 2 * margin + columns * tile_w + (columns - 1) * gap
    img = background(width, height, glow=(0.5, 0.0))
    draw = ImageDraw.Draw(img)
    title, label = font("Barlow-SemiBold", 36), font("Barlow-Medium", 26)
    y = margin
    for section, row in tiles:
        draw.text((margin, y), section, font=title, fill=ACCENT)
        y += head
        for i, (name, crop) in enumerate(row):
            col, line = i % columns, i // columns
            x = margin + col * (tile_w + gap)
            ty = y + line * (tile_h + caption + gap // 2)
            img.paste(crop, (x, ty))
            draw.text((x + 12, ty + tile_h + 6), name, font=label, fill=TX_1)
        lines = -(-len(row) // columns)
        y += lines * (tile_h + caption) + (lines - 1) * gap // 2 + gap
    return img


def _m(kind: str, col: int, row: int, cols: int = 1, rows: int = 1, **options) -> dict:
    return {"module": kind, "col": col, "row": row, "cols": cols, "rows": rows, **options}


def new_module_layouts() -> Image.Image:
    """The newer modules on panels: an everyday bar, a hardware bar, two small panels."""
    everyday = [_m("music", 0, 0, 3), _m("world", 0, 1, 3), _m("agenda", 3, 0, 2, 2),
                _m("sun", 5, 0, 3), _m("countdown", 5, 1, 2, target="12-24", title="Christmas"),
                _m("analog", 7, 1)]  # fmt: skip
    hardware = [_m("cores", 0, 0, 3), _m("temps", 0, 1, 3), _m("game", 3, 0, 3),
                _m("processes", 3, 1, 3), _m("drives", 6, 0, 2), _m("values", 6, 1, 2)]  # fmt: skip
    bars = [
        ("Everyday · Sunset", module_frame("turing-9.2-usb", "sunset", everyday)[0]),
        ("Hardware · Neon", module_frame("turing-9.2-usb", "neon", hardware)[0]),
    ]
    small_specs = [
        ('3.5" · Paper', "turing-3.5", "paper",
         [_m("music", 0, 0, 2), _m("analog", 2, 0), _m("agenda", 0, 1, 2), _m("battery", 2, 1)]),
        ('5" · Graphite', "turing-5", "graphite",
         [_m("game", 0, 0, 2), _m("temps", 2, 0, 1, 2), _m("cores", 0, 1, 2)]),
        ('3.5" portrait · Arctic', "turing-3.5", "arctic",
         [_m("sun", 0, 0, 2), _m("world", 0, 1, 2), _m("netinfo", 0, 2, 2)]),
    ]  # fmt: skip
    small = []
    for name, model, look, modules in small_specs:
        side = "portrait" if "portrait" in name else "landscape"
        small.append((name, module_frame(model, look, modules, side)[0]))
    border, margin, gap, caption = 12, 44, 40, 56
    label = font("Barlow-SemiBold", 28)
    m = shadow_margin(border)
    bar_w, hh = 940, 300
    width = 2 * margin + 2 * (bar_w + 2 * border) + gap
    row1 = bar_w // 4 + 2 * border
    height = margin + row1 + caption + gap // 2 + hh + 2 * border + caption + margin // 2
    img = background(width, height, glow=(0.5, 0.0)).convert("RGBA")
    draw = ImageDraw.Draw(img)
    for i, (name, frame) in enumerate(bars):
        x = margin + i * (bar_w + 2 * border + gap)
        dev = device(frame.resize((bar_w, bar_w // 4), Image.LANCZOS), border)
        img.alpha_composite(dev, (x - m, margin - m))
        draw.text((x + 4, margin + row1 + 10), name, font=label, fill=TX_1)
    sized = [(n, f.resize((round(f.width * hh / f.height), hh), Image.LANCZOS)) for n, f in small]
    total = sum(f.width + 2 * border for _, f in sized)
    spacing = (width - 2 * margin - total) / (len(sized) - 1)
    x, y = margin, margin + row1 + caption + gap // 2
    for name, frame in sized:
        img.alpha_composite(device(frame, border), (round(x) - m, y - m))
        # (a caption wider than its panel stays inside the picture)
        tx = min(x + 4, width - margin - draw.textlength(name, font=label))
        draw.text((tx, y + hh + 2 * border + 10), name, font=label, fill=TX_1)
        x += frame.width + 2 * border + spacing
    return img.convert("RGB")


# The computer of the app's pictures: no panel on the bus, a processor and a graphics card with
# plain names (LP_HARDWARE), the demo's readings and EXTRA at the time of the other pictures
# (LP_NOW, LP_DEMO_READINGS): not the clock, the sensors and the names of whoever runs this.
NEUTRAL_MACHINE = {"cpu": "8-Core Processor", "adapters": [["Graphics Card", 8 * 2**30, True]]}


@contextlib.contextmanager
def rust_app(home: Path, *command: str, extra: bool = True):
    """Libre Panel (the program built in rust/, tools/lp_check.py) run with ``command`` in the
    settings folder ``home``, on the neutral computer (``extra``: :data:`EXTRA` over the demo's
    readings in its previews): the address it says it runs at (with its token after "#token=").
    It is stopped at the end."""
    import signal
    import subprocess

    from lp_check import app_program

    (home / "bus").write_text("", encoding="utf-8")  # (the USB bus and the ports: nothing)
    (home / "machine.json").write_text(json.dumps(NEUTRAL_MACHINE), encoding="utf-8")
    (home / "readings.json").write_text(json.dumps(EXTRA), encoding="utf-8")
    english = {"LANG": "en_US.UTF-8", "LANGUAGE": "", "LC_ALL": "", "LC_MESSAGES": ""}
    env = {
        **os.environ, **libraries(), **english,
        "LIBRE_PANEL_HOME": str(home), "LIBRE_PANEL_DATA": str(ROOT / "data"),
        "XDG_CONFIG_HOME": str(home / "xdg"), "LP_TEST_BUS": str(home / "bus"),
        "LP_HARDWARE": str(home / "machine.json"), "LP_NOW": repr(WHEN),
        "LP_SETUP_SYSTEM": "other", "LP_LINUX_ROOT": "/no/such/root",
        "LP_COMMUNITY_INDEX": "http://127.0.0.1:9/community/themes/index.json",
    }  # fmt: skip
    if extra:
        env["LP_DEMO_READINGS"] = str(home / "readings.json")
    process = subprocess.Popen([str(app_program()), *command], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, encoding="utf-8")  # fmt: skip
    said = []
    first = process.stdout.readline()
    for stream in (process.stdout, process.stderr):  # (read as it is said: no pipe fills up)
        threading.Thread(target=said.extend, args=(stream,), daemon=True).start()
    try:
        if " running at " not in first:
            raise RuntimeError(f"Libre Panel did not start: {first}{''.join(said)}")
        yield first.split(" running at ")[1].split()[0]
    finally:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()


def with_token(address: str, route: str) -> str:
    """The app's ``route`` ("#/design") at ``address`` (as the app says it, its token after
    "#token="), the token after the route, as the window gives it to the page."""
    page, token = address.split("#token=")
    return f"{page}{route}&token={token}"


def editor_shots(out: Path) -> None:
    """The app in a browser: a new theme from a layout, Design with a ring selected, a module
    dragged onto free cells, the command palette, Start's gallery and Settings."""
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "libre-panel"  # a home of its own, with a plain name
    home.mkdir()
    (home / "config.toml").write_text('language = "en"\ntheme = "lumen"\n', encoding="utf-8")
    modules = "window.libre.doc.widgets().filter(window.libre.doc.isModule).length"
    shown = "[...document.querySelectorAll('%s')].every(i => i.complete && i.naturalWidth > 0)"
    with rust_app(home, "editor", "--port", "0") as address, sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1600, "height": 900},
            device_scale_factor=1.5,
            locale="en-US",
            bypass_csp=True,  # for the waits below
        )
        page = context.new_page()
        page.goto(with_token(address, "#/design"))
        page.wait_for_selector(".panel-canvas .preview")
        page.wait_for_timeout(500)
        # a new theme: starting layouts for this panel (the 9.2" bar) and six looks
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("new theme")
        page.keyboard.press("Enter")
        page.wait_for_selector(".dialog .template-card")
        page.wait_for_function(shown % ".dialog .template-picture img")
        page.wait_for_function(
            "document.querySelectorAll('.dialog .template-picture img').length === 4"
        )
        page.wait_for_timeout(500)
        page.locator(".dialog").screenshot(path=str(out / "editor-new.png"))
        page.locator(".dialog .btn-primary").click()
        page.wait_for_function(f"{modules} === 5")
        page.wait_for_timeout(1500)
        # Design with a ring selected: its handles, the toolbar over it, its settings
        page.evaluate("window.libre.doc.setSelection(['ring-4'])")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(out / "editor.png"))
        # a module from the library, dragged onto free cells
        page.evaluate("window.libre.doc.setSelection(['network-2'])")
        page.keyboard.press("Delete")
        page.wait_for_timeout(1000)
        tile = page.locator(".tile[data-item='module:graph']").bounding_box()
        grid = page.evaluate("window.libre.store.get().render.grid")
        canvas = page.locator(".panel-canvas").bounding_box()
        zoom = canvas["width"] / page.evaluate("window.libre.store.get().render.size[0]")
        x = grid["x"] + (grid["cell_w"] + grid["gap"]) + grid["cell_w"] / 2
        y = grid["y"] + (grid["cell_h"] + grid["gap"]) + grid["cell_h"] / 2
        page.mouse.move(tile["x"] + 30, tile["y"] + 20)
        page.mouse.down()
        page.mouse.move(tile["x"] + 80, tile["y"] + 40, steps=4)
        page.mouse.move(canvas["x"] + x * zoom, canvas["y"] + y * zoom, steps=12)
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "editor-drag.png"))
        page.mouse.up()
        for close in page.locator(".toast .toast-close").all():  # ("Removed ...": not after)
            close.click()
        # the command palette finds everything by a few letters: blocks, settings, themes
        page.evaluate("window.libre.doc.setSelection([])")
        page.keyboard.press("Control+k")
        page.locator(".palette input").fill("weather")
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "palette.png"))
        page.keyboard.press("Escape")
        # Start: every theme, each drawn by the panel's own renderer
        page.evaluate("location.hash = '#/home/themes'")
        page.wait_for_selector(".start.gallery .theme-card")
        page.wait_for_function(shown % ".theme-card img")
        page.mouse.move(0, 0)  # (a card under the pointer shows its pencil)
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "start.png"))
        # Settings
        page.evaluate("location.hash = '#/settings/services'")
        page.wait_for_selector(".settings-card.service")
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "settings.png"))
        browser.close()


def panels_photo(panels: list[tuple[str, str, tuple[float, float]]], wait_s: float = 90.0):
    """The app's own *Picture of all panels* (``GET /api/panels/photo``) of ``panels`` (model,
    theme, where it stands in millimetres): the background app runs them as virtual panels on
    the demo's readings alone, the time and they standing still at :data:`WHEN` (LP_NOW), on the
    neutral computer."""
    import time
    import urllib.error
    import urllib.request

    home = Path(tempfile.mkdtemp()) / "libre-panel"  # a home of its own, with a plain name
    home.mkdir()
    (first_model, first_theme, first_at), *more = panels
    lines = [
        f'language = "en"\ntheme = "{first_theme}"\nfps = 5\n',
        f'[device]\ndriver = "virtual"\nmodel = "{first_model}"\nat = {list(first_at)}\n',
        '[sensors]\nproviders = ["demo"]\n',
    ]
    for model, theme, at in more:
        lines.append(f'[[panels]]\ndriver = "virtual"\nmodel = "{model}"\ntheme = "{theme}"\n'
                     f"at = {list(at)}\n")  # fmt: skip
    (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")
    with rust_app(home, "start", "--port", "0", "--no-browser") as address:
        url, token = address.split("#token=")
        url = url.split("#")[0]

        def asked(route: str) -> bytes:
            request = urllib.request.Request(url + route, headers={"X-Libre-Panel": token})
            with urllib.request.urlopen(request, timeout=60) as answer:
                return answer.read()

        ends, before = time.monotonic() + wait_s, None
        while True:  # each panel shows its theme, and the same twice: it has come in, whole
            panel = json.loads(asked("api/app"))["panel"]
            states = [panel.get("state"), *(m.get("state") for m in panel.get("more", []))]
            themes = [panel.get("theme"), *(m.get("theme") for m in panel.get("more", []))]
            shown = len(states) == len(panels) and all(s == "showing" for s in states)
            if shown and (panel.get("frames") or 0) > 5 and themes == before:
                break
            if time.monotonic() > ends:
                raise RuntimeError(f"the panels did not show their themes: {states} {themes}")
            before = themes if shown else None
            time.sleep(2.0)
        try:
            data = asked("api/panels/photo")
        except urllib.error.HTTPError as error:
            raise RuntimeError(json.loads(error.read() or b"{}").get("error", error)) from error
    return Image.open(io.BytesIO(data)).convert("RGB")


def more_panels() -> Image.Image:
    """Two bars, one above the other as under a monitor: Lumen and Lumen Companion beside it,
    in the picture the app's *Picture of all panels* saves (the first in Lumen's pair version,
    as the app shows it while a companion runs beside it)."""
    model = parts((("turing-9.2-usb", "landscape"),))["models"]["turing-9.2-usb"]
    w, h = model["native"][1], model["native"][0]  # (landscape: the longer side across)
    h *= model["inch"] * 25.4 / math.hypot(w, h)  # (its height in millimetres: arrangement.rs)
    # (where a panel stands is said in whole millimetres)
    return panels_photo([("turing-9.2-usb", "lumen", (0, 0)),
                         ("turing-9.2-usb", "lumen-companion", (0, round(h + 30)))])  # fmt: skip


# Three panels where they stand (millimetres, top left corners): the 12.3" with Lumen, and under
# it the 9.2" bar with Lumen Companion beside the 5.2" with Encore. Lumen's version for the 12.3"
# has the weather where the bar's has what plays, which Encore shows; its companion has every core,
# the fans, the drives and the processes.
PANELS_AT_ONCE = (
    ("turing-12.3-usb", "lumen", (35, 0)),
    ("turing-9.2-usb", "lumen-companion", (0, 125)),
    ("turing-5.2-usb", "encore", (247, 121)),
)  # fmt: skip
PANELS_WIDTH = 2000  # the picture of all panels is up to 4000 pixels across: more than a page shows


def panels_at_once() -> Image.Image:
    """The app's own *Picture of all panels* of :data:`PANELS_AT_ONCE`, made smaller."""
    picture = panels_photo(list(PANELS_AT_ONCE))
    height = round(picture.height * PANELS_WIDTH / picture.width)
    return picture.resize((PANELS_WIDTH, height), Image.Resampling.LANCZOS)


# The feature grid of the README and the website: a still of each feature, small and in one
# shape (the first screen loads them all: no clips there). Each is a part of a clip's poster in
# docs/media (tools/readme_media.py makes them: run it first) or of a picture here, as
# (folder, file, (left, top, right, bottom) as shares of it), cut to the tile's shape round the
# middle of that part.
TILE_SIZE = (600, 300)
TILES = {
    "tile-video-mode.png": ("media", "close-up-rings.png", (0, 0, 1, 1)),
    "tile-panels.png": ("images", "panels-at-once.png", (0.06, 0.08, 0.96, 0.92)),
    "tile-cards.png": ("media", "card-ignition.png", (0, 0, 0.58, 1)),
    "tile-notices.png": ("media", "volume-looks.png", (0.42, 0, 1, 1)),
    "tile-game.png": ("media", "game-entrance.png", (0, 0, 0.58, 1)),
    "tile-screens.png": ("media", "encore.png", (0, 0, 0.58, 1)),
    "tile-screens-in-turn.png": ("media", "screens-in-turn.png", (0, 0, 0.58, 1)),
    "tile-start-screen.png": ("media", "start-screen.png", (0.21, 0, 0.79, 1)),
    "tile-vertical.png": ("images", "vertical.png", (0, 0, 1, 1)),
    "tile-agents.png": ("images", "agents.png", (0, 0, 0.5, 1)),
    "tile-sizes.png": ("images", "gallery.png", (0, 0.43, 1, 1)),
    "tile-app.png": ("media", "app-drag-module.png", (0, 0, 1, 0.889)),
}  # fmt: skip


def tile(source: Image.Image, part: tuple[float, float, float, float]) -> Image.Image:
    """``part`` of ``source`` in the shape of a tile, round its middle, at :data:`TILE_SIZE`."""
    w, h = TILE_SIZE
    left, top = part[0] * source.width, part[1] * source.height
    right, bottom = part[2] * source.width, part[3] * source.height
    if (right - left) / (bottom - top) > w / h:  # wider than a tile: narrower, round its middle
        cut = (right - left - (bottom - top) * w / h) / 2
        left, right = left + cut, right - cut
    else:
        cut = (bottom - top - (right - left) * h / w) / 2
        top, bottom = top + cut, bottom - cut
    box = (round(left), round(top), round(right), round(bottom))
    return source.convert("RGB").crop(box).resize(TILE_SIZE, Image.Resampling.LANCZOS)


def tiles(out: Path, media: Path) -> None:
    """The stills of :data:`TILES` in ``out``, of the pictures in ``out`` and the posters in
    ``media``."""
    for name, (folder, source, part) in TILES.items():
        path = (media if folder == "media" else out) / source
        if not path.is_file():
            raise FileNotFoundError(f"{name} is made of {path}: make that first")
        with Image.open(path) as picture:
            tile(picture, part).save(out / name, optimize=True)


# -- the README's own parts ------------------------------------------------------------------


def app_icon(name: str) -> str:
    """The drawing of one of the app's icons (data/editor/app/ui/icons.js): the
    inside of its 24 x 24 SVG, stroked in the text colour (its strings, and its dots, as the
    app's ``DOT(x, y)`` draws them, in the order they come)."""
    source = (ROOT / "data/editor/app/ui/icons.js").read_text(encoding="utf-8")
    key = re.search(rf"^  \"?{re.escape(name)}\"?:", source, re.M)
    if key is None:
        raise KeyError(f"the app has no icon {name!r}")
    rest = source[key.end() :]
    following = re.search(r"^  \"?[a-z0-9-]+\"?:|^};", rest, re.M)
    entry = rest[: following.start() if following else len(rest)]
    parts = []
    for text, x, y in re.findall(r"'([^']*)'|DOT\(([\d.]+),\s*([\d.]+)\)", entry):
        parts.append(
            text or f'<circle cx="{x}" cy="{y}" r="1.3" fill="currentColor" stroke="none"/>'
        )
    return "".join(parts)


def icon_tile(name: str) -> str:
    """One of the app's icons as the README's overview shows it: lit in the accent on a dark
    tile with rounded corners, which reads on GitHub's light pages and its dark ones alike."""
    drawing = app_icon(name)
    accent = "#2EE6D6"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56" viewBox="0 0 56 56">'
        '<defs><linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#17222e"/><stop offset="1" stop-color="#0b1118"/>'
        '</linearGradient><filter id="glow" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="1.6"/></filter></defs>'
        '<rect x="0.75" y="0.75" width="54.5" height="54.5" rx="14" fill="url(#tile)" '
        'stroke="#2b3846" stroke-width="1.5"/>'
        f'<g transform="translate(14 14) scale(1.1667)" fill="none" stroke="{accent}" '
        f'color="{accent}" '
        'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
        f'<g opacity="0.55" filter="url(#glow)">{drawing}</g>{drawing}</g></svg>\n'
    )


ISOLATE_BUTTONS = """() => {
  const actions = document.querySelector('.hero .actions');
  const holder = document.createElement('div');
  holder.id = '__buttons';
  holder.appendChild(actions);
  document.body.appendChild(holder);
  const style = document.createElement('style');
  style.textContent = 'html,body{background:transparent!important}' +
    'body>*:not(svg):not(#__buttons){display:none!important}' +
    '#__buttons{position:absolute;left:40px;top:40px}' +
    '#__buttons .actions{display:flex;gap:60px;margin:0}' +
    '*{transition:none!important;animation:none!important}';
  document.head.appendChild(style);
}"""


def buttons(out: Path) -> None:
    """The website's two download buttons (for Windows, for Linux), in English and German, each
    as it is on the website's first screen, its glow with it, on a clear background."""
    import check_pages
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="readme-buttons-") as scratch:
        site = Path(scratch) / "site"
        check_pages.build(ROOT, site)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for language in ("en", "de"):
                    page = browser.new_page(viewport={"width": 1280, "height": 600},
                                            device_scale_factor=2, bypass_csp=True)  # fmt: skip
                    index = site / ("de" if language == "de" else "") / "index.html"
                    page.goto(index.as_uri())
                    page.evaluate(ISOLATE_BUTTONS)
                    page.wait_for_timeout(200)
                    for name, (lang, asset) in BUTTONS.items():
                        if lang != language:
                            continue
                        box = page.locator(f'#__buttons a[data-asset="{asset}"]').bounding_box()
                        room = 22  # (the glow under the primary one)
                        clip = {
                            "x": box["x"] - room,
                            "y": box["y"] - room // 2,
                            "width": box["width"] + 2 * room,
                            "height": box["height"] + 2 * room,
                        }
                        page.screenshot(path=str(out / name), clip=clip, omit_background=True)
                    page.close()
            finally:
                browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", default="docs/images", type=Path)
    parser.add_argument("--media", default="docs/media", type=Path, metavar="DIR",
                        help="the clips' posters for the tiles (default docs/media)")  # fmt: skip
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="make only these pictures (as pictures() names them)")  # fmt: skip
    args = parser.parse_args()
    out = args.out
    if unknown := sorted(set(args.only or ()) - set(pictures())):
        parser.error(f"no such picture: {', '.join(unknown)}")

    def wanted(*names: str) -> bool:
        return args.only is None or bool(set(names) & set(args.only))

    out.mkdir(parents=True, exist_ok=True)
    for theme_id in THEMES:  # true pixels, as the panel shows them
        if wanted(f"{theme_id}.png"):
            render(theme_id).save(out / f"{theme_id}.png", optimize=True)
    big: dict[str, Image.Image] = {}

    def sharp(theme_id: str) -> Image.Image:  # sharp when scaled
        if theme_id not in big:
            big[theme_id] = render(theme_id, 2)
        return big[theme_id]

    if wanted("hero.png"):
        hero(sharp("lumen")).save(out / "hero.png", optimize=True)
    # Studio with nothing playing: the weather in its card, Lumen above it with the track
    media = tuple(k for k in reading_keys() if k.startswith("media."))
    if wanted("gallery.png"):
        shots = {theme_id: sharp(theme_id) for theme_id in THEMES}
        gallery({**shots, "studio": render("studio", 2, media)}).save(
            out / "gallery.png", optimize=True
        )
    if wanted("social-preview.png"):
        social(sharp("lumen")).save(out / "social-preview.png", optimize=True)
    # (the card shows what plays before the weather: without either, it shows the day)
    quiet = tuple(k for k in reading_keys() if k.startswith(("weather.", "media.")))
    gpu = tuple(k for k in reading_keys() if k.startswith("gpu."))
    if wanted("studio-adapts.png"):
        adapts(
            [
                (
                    render("studio", 2, quiet),
                    "Nothing playing, weather off",
                    "· the day takes the card",
                ),
                (render("studio", 2, gpu), "No GPU sensor", "· the disk takes its ring"),
            ]
        ).save(out / "studio-adapts.png", optimize=True)
    made = {
        "vertical.png": vertical,
        "module-sizes.png": module_sizes,
        "weather-sizes.png": weather_sizes,
        "looks.png": looks,
        "module-layouts.png": module_layouts,
        "module-catalog.png": module_catalog,
        "module-layouts-more.png": new_module_layouts,
        "more-panels.png": more_panels,
        "panels-at-once.png": panels_at_once,
        "round-screens.png": round_screens,
        "zc360-screens.png": zc360_screens,
    }
    for name, make in made.items():
        if wanted(name):
            make().save(out / name, optimize=True)
    if wanted(*APP_SHOTS):
        try:
            editor_shots(out)
        except ImportError:
            print("Playwright is not installed: the editor pictures are left as they are")
    for name, icon in ICONS.items():
        if wanted(name):
            (out / name).write_text(icon_tile(icon), encoding="utf-8")
    if wanted(*BUTTONS):
        try:
            buttons(out)
        except ImportError:
            print("Playwright is not installed: the download buttons are left as they are")
    if wanted(*TILES):  # (last: they are made of the pictures above)
        tiles(out, args.media)
    print(f"wrote the images to {out}")


if __name__ == "__main__":
    main()
