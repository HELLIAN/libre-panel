"""Write the icon files from the logo code: python packaging/make_icons.py

The logo is drawn by Libre Panel itself (rust/render/src/branding.rs): lp-check, the program
`cargo build --release` makes in rust/ (LIBRE_PANEL_CHECK names another), draws it at each
size, and Pillow writes the files.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def lp_check() -> Path:
    named = os.environ.get("LIBRE_PANEL_CHECK")
    if named:
        return Path(named)
    built = (
        ROOT / "rust" / "target" / "release" / ("lp-check.exe" if os.name == "nt" else "lp-check")
    )
    if not built.is_file():
        raise SystemExit("needs lp-check: cargo build --release in rust/ (or LIBRE_PANEL_CHECK)")
    return built


def logos(sizes: list[int]) -> dict[int, Image.Image]:
    """The app's icon at each size, as Libre Panel draws it (branding.rs `logo`)."""
    cases = "".join(json.dumps({"op": "logo", "size": size}) + "\n" for size in sizes)
    done = subprocess.run([str(lp_check())], input=cases, capture_output=True, text=True,
                          encoding="utf-8", check=True)  # fmt: skip
    drawn = {}
    for size, line in zip(sizes, done.stdout.splitlines(), strict=True):
        image = json.loads(line)["image"]
        data = base64.b64decode(image["data"])
        drawn[size] = Image.frombytes(image["mode"], tuple(image["size"]), data)
    return drawn


def main() -> None:
    logo = logos(sorted({64, 256, 512, *ICO_SIZES}))
    logo[256].save(ROOT / "data/assets/libre-panel.png", optimize=True)
    logo[64].save(ROOT / "data/editor/static/icon.png", optimize=True)
    icons = ROOT / "packaging/icons"
    icons.mkdir(exist_ok=True)
    logo[512].save(icons / "libre-panel-512.png", optimize=True)
    logo[256].save(
        icons / "libre-panel.ico",
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[logo[s] for s in ICO_SIZES if s != 256],
    )


if __name__ == "__main__":
    main()
