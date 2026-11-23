"""What the plugin host draws the plugins' widget types and screens with for the Rust app
(plugin_host.py), and what their RenderContext reads: the theme as the app tells it (its
size, palette, font, model, folder and screen), its fonts and colours, the frame's timing and
the frame shown, as the Python app's renderer gave them, without that renderer.
A widget type's piece goes as it drew it: the widget's effects (its opacity, shadow and glow)
are the app's, as on its own widgets (lp-render's Renderer::effects, _effects' twin)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from PIL import Image, ImageColor, ImageFont

from libre_panel.fonts import DEFAULT_FONT, builtin_font_path
from libre_panel.fonts.fallback import truetype

log = logging.getLogger(__name__)

# How much larger a plugin draws to downsample (context.supersample), as Libre Panel's own
# widgets do; and the colour that is the cover's on show (theme.model's COVER_COLOR).
SUPERSAMPLE = 3
COVER_COLOR = "@cover"

RGBA = tuple[int, int, int, int]


@dataclass
class PluginTheme:
    """The theme a drawing is for, as much of it as the plugins see."""

    name: str
    width: int
    height: int
    model: str = "custom"
    palette: dict[str, str] = field(default_factory=dict)
    font: str = DEFAULT_FONT
    # {"name": ..., "options": {...}}: the screen a plugin draws
    screen: dict[str, Any] | None = None
    root: Path | None = None

    @property
    def orientation(self) -> str:
        return "landscape" if self.width > self.height else "portrait"


def theme_file(root: Path, rel: str) -> Path:
    """The theme's file ``rel`` (a font of its own), refused outside its folder as
    rust/theme/src/model.rs's resolve_asset refuses it."""
    if not rel or not isinstance(rel, str):
        raise ValueError("empty asset path")
    if Path(rel).is_absolute() or PureWindowsPath(rel).drive or rel.startswith(("/", "\\")):
        raise ValueError(f"asset path must be relative to the theme folder: {rel!r}")
    base = root.resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"asset path leaves the theme folder: {rel!r}")
    return target


class PluginRenderer:
    """A theme's drawing of the plugins' widget types and its screen: what their contexts
    read of it, the frame's timing (the app sets it per frame), what they warned of and
    whether they move."""

    def __init__(self, theme: PluginTheme, animate: bool = False, preview: bool = False) -> None:
        self.theme = theme
        self.animate = animate
        self.preview = preview  # an editor preview or a picture, not the panel
        self.shown: Image.Image | None = None  # the frame the panel shows (the app's)
        self.toast: tuple[Any, float] | None = None  # the toast on show, its age (the app's)
        self.snapshot: Any = None  # the readings it draws (context.image, context.state)
        self.warnings: list[str] = []
        self.moving = False  # a part asked for the next frame at full rate
        self.fps = 0
        self.continuous = False  # video mode: every frame reaches the panel
        self.sample: tuple[float, float] = (0.0, 1.0)  # (the last reading, seconds to the next)
        self._fonts: dict[tuple[str, int], Any] = {}
        self._widget_types: dict[str, Any] = {}
        self._kept: dict[str, tuple[Any, Any]] = {}  # widget id: (its key, its piece)
        self.screen = self._make_screen()

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
            log.warning(message)

    # -- what a context reads ----------------------------------------------------------------

    def color(self, value: str | None, alpha_scale: float = 1.0) -> RGBA | None:
        if value is None:
            return None
        if value == COVER_COLOR:  # the colour the cover on show carries, else the accent
            from libre_panel.render import cover

            images = getattr(self.snapshot, "images", None) or {}
            carried = cover.dominant(images.get("media.cover"))
            if carried is not None:
                return (*carried, int(255 * alpha_scale))
            value = self.theme.palette.get("accent", "#22d3ee")
        elif value.startswith("@"):
            value = self.theme.palette.get(value[1:], "#ff00ff")
        rgb = ImageColor.getrgb(value)
        alpha = rgb[3] if len(rgb) == 4 else 255
        return (rgb[0], rgb[1], rgb[2], int(alpha * alpha_scale))

    def font(self, ref: str, size: int) -> Any:
        ref = ref or self.theme.font or DEFAULT_FONT
        size = max(4, min(int(size), 512))
        key = (ref, size)
        if key not in self._fonts:
            font = None
            try:
                builtin = builtin_font_path(ref)
                if builtin is not None:
                    font = truetype(str(builtin), size)
                elif self.theme.root is not None:
                    font = truetype(str(theme_file(self.theme.root, ref)), size)
            except (OSError, ValueError) as exc:
                self._warn(f"font {ref!r} could not be loaded ({exc}); using the default font")
            if font is None and ref != DEFAULT_FONT:
                font = truetype(str(builtin_font_path(DEFAULT_FONT)), size)
            self._fonts[key] = font or ImageFont.load_default(size)
        return self._fonts[key]

    def progress(self, now: float, delay: float = 0.0) -> float:
        """How far ``now`` is from the last reading to the next, 0 to 1."""
        at, interval = self.sample
        return min(1.0, max(0.0, (now - at - delay) / interval))

    @staticmethod
    def _down(layer: Image.Image, w: int, h: int, s: int = SUPERSAMPLE) -> Image.Image:
        """A layer drawn ``s`` times larger reduced to its size on the panel: an exact
        reduction averages each block (with premultiplied alpha, so edges do not darken),
        any other is Lanczos."""
        w, h = max(1, w), max(1, h)
        if layer.size == (w * s, h * s):
            if layer.mode == "RGBA":
                return layer.convert("RGBa").reduce(s).convert("RGBA")
            return layer.reduce(s)  # masks ("L")
        return layer.resize((w, h), Image.Resampling.LANCZOS)

    # -- the plugins' parts ------------------------------------------------------------------

    def _make_screen(self) -> Any:
        if self.theme.screen is None:
            return None
        from libre_panel.plugins.loader import registry
        from libre_panel.plugins.render import RenderContext

        name = self.theme.screen["name"]
        cls = registry().get("screens", name)
        if cls is None:
            return None  # the app has warned
        try:
            return cls(RenderContext(self, cls), dict(self.theme.screen["options"]))
        except Exception as exc:  # a broken screen must not blank the panel
            self._warn(f"screen {name!r} failed to start: {exc}")
            return None

    def screen_image(self, snapshot: Any, now: float) -> Image.Image | None:
        """The screen's picture of this frame; None where it failed."""
        try:
            image = self.screen.render(snapshot, now)
        except Exception as exc:
            self._warn(f"screen {self.theme.screen['name']!r} failed: {exc}")
            return None
        if getattr(self.screen, "moving", False):
            self.moving = True
        return image

    def widget_piece(self, widget: dict[str, Any], snapshot: Any, now: float) -> Any:
        """What a plugin's widget type draws of ``widget``: ``(layer, x, y)`` (RGBA), kept,
        the same object, while the widget and its type's key stay the same; None where it
        draws nothing or no plugin has the type (the app has warned)."""
        wtype = widget["type"]
        if wtype not in self._widget_types:
            from libre_panel.plugins.loader import registry
            from libre_panel.plugins.render import RenderContext

            cls = registry().get("widgets", wtype)
            self._widget_types[wtype] = (cls(), RenderContext(self, cls)) if cls else None
        entry = self._widget_types[wtype]
        if entry is None:
            return None
        kind, context = entry
        content = (widget, kind.key(widget, snapshot, now))
        kept = self._kept.get(widget["id"])
        if kept is not None and kept[0] == content:
            return kept[1]
        result = kind.draw(widget, context, snapshot, now)
        if result is None:
            self._kept.pop(widget["id"], None)
            return None
        at = (widget["x"], widget["y"])
        image, (x, y) = result if isinstance(result, tuple) else (result, at)
        piece = (image if image.mode == "RGBA" else image.convert("RGBA"), int(x), int(y))
        self._kept[widget["id"]] = (content, piece)
        return piece

    def close(self) -> None:
        """The screen closed, and the contexts of the widget types let go (they refer to
        this drawing)."""
        if self.screen is not None:
            try:
                self.screen.close()
            except Exception as exc:
                log.warning("screen %s: %s", self.theme.screen["name"], exc)
            self.screen = None
        self._widget_types.clear()
        self._kept.clear()
