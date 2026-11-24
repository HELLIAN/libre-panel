"""The Libre Panel mark (rust/render/src/branding.rs): drawn once, the same everywhere it is
shown: the app's icon files, its header's and the website's SVG, the panel's own screens."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from rust_check import picture, run

ROOT = Path(__file__).resolve().parent.parent


def test_the_bars_stand_clear_of_the_ring():
    """0.5.6's tallest bar cut into the ring: every bar keeps room to its inner edge."""
    (said,) = run([{"op": "mark_svg"}])
    assert said["clearance"] >= 0.15


def test_the_svg_of_the_app_and_the_website_is_the_mark_as_drawn_in_code():
    (said,) = run([{"op": "mark_svg", "box": 24.0, "margin": 0.7}])
    svg = said["svg"]
    assert "<path" in svg and svg.count("<rect") == 3
    icons = (ROOT / "data/editor/app/ui/icons.js").read_text(encoding="utf-8")
    assert f"logo: '{svg}'," in icons
    for page in ("site/index.html", "site/de/index.html"):
        text = (ROOT / page).read_text(encoding="utf-8")
        assert f'<symbol id="i-logo" viewBox="0 0 24 24">{svg}</symbol>' in text, page


@pytest.mark.parametrize(
    "path, size",
    [
        ("data/assets/libre-panel.png", 256),
        ("data/editor/static/icon.png", 64),
        ("packaging/icons/libre-panel-512.png", 512),
    ],
)
def test_the_icon_files_are_the_logo_as_drawn_in_code(path, size):
    """python packaging/make_icons.py writes them: run it after a change to the mark."""
    written = np.asarray(Image.open(ROOT / path).convert("RGBA"), np.int16)
    (said,) = run([{"op": "logo", "size": size}])
    drawn = np.asarray(picture(said["image"]), np.int16)
    assert written.shape == drawn.shape
    assert np.abs(written - drawn).max() <= 1


def test_the_windows_icon_has_every_size_of_the_logo():
    ico = Image.open(ROOT / "packaging/icons/libre-panel.ico")
    assert {(s, s) for s in (16, 32, 48, 256)} <= ico.ico.sizes()


def test_the_mark_sits_in_the_middle_of_its_box():
    """The ring is open below: it is drawn lower, so that it is as far from the top as its
    open ends are from the bottom."""
    (said,) = run([{"op": "mark", "diameter": 200, "light": 1.0}])
    alpha = np.asarray(picture(said["image"]).getchannel("A"))
    rows = np.where(alpha.max(axis=1) > 128)[0]
    above, below = rows[0], alpha.shape[0] - 1 - rows[-1]
    assert abs(int(above) - int(below)) <= 3
