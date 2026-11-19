"""Write the v2 screens (Lumen v2, Arena v2, Encore v2) from their first versions, or check
that they are as those make them.

    python tools/themes_v2.py          # what differs; exit 1 if anything does
    python tools/themes_v2.py --write  # make every version of each v2 screen from its own

A v2 screen is its screen over a light field (render/field.py): the same screens, in every
version the first has (each size, on end, the pair versions beside a companion, the
companions in both directions), each made from that version:

- its name: the screen's with " v2" after it ("Lumen 12.3″" -> "Lumen v2 12.3″");
- its background: black, the light over it (the light is the ground: no picture, no
  gradient behind the cards); the light drifts while the switch "light_flows" is on and
  follows the load while "light_load" is (both on by default);
- its cards a little more see-through, so the light shows through them (design.FAMILIES'
  "<screen>-v2");
- Encore v2: the light in its cover's colours while a track plays (render/cover.py's
  colours), in place of the cover blurred behind the whole screen;
- everything else as the first has it.

The themes are written as they are kept: JSON, two spaces in, a line at the end.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from libre_panel.design import FAMILIES  # noqa: E402

THEMES = ROOT / "data" / "themes"

# each v2 screen: its light's look, the picture whose colours it takes ("" none), and the
# widgets of the first it leaves out (the light takes their place)
SCREENS: dict[str, dict[str, Any]] = {
    "lumen": {"look": "lumen", "colours": "", "without": ()},
    "arena": {"look": "arena", "colours": "", "without": ()},
    "encore": {"look": "lumen", "colours": "@media.cover", "without": ("ambient",)},
}
SWITCHES = {
    "light_flows": {"label": "The light flows", "on": True},
    "light_load": {"label": "The light follows the load", "on": True},
}
# the versions of a screen: its name, or its name and an ending ("-1920x720", "-vertical",
# "-pair...", "-companion...")
VERSION = re.compile(r"^(?P<screen>[a-z]+)(?P<ending>(-(\d+x\d+|vertical|pair|companion))*)$")
WORDING = {
    "lumen": " Over a light that drifts across your panels and warms with the load.",
    "arena": " Over a light that drifts across your panels and warms with the load.",
    "encore": " Over a light in the colours of the cover that drifts across your panels.",
}


def versions() -> list[tuple[str, str]]:
    """Each first version a v2 version is made of, and the v2 version's name."""
    out = []
    for folder in sorted(THEMES.iterdir()):
        found = VERSION.match(folder.name)
        if found and found["screen"] in SCREENS and (folder / "theme.json").is_file():
            out.append((folder.name, f"{found['screen']}-v2{found['ending']}"))
    return out


def made(screen: str, data: dict[str, Any]) -> dict[str, Any]:
    """The v2 version's theme.json of ``screen``'s version ``data``."""
    spec = SCREENS[screen]
    out = json.loads(json.dumps(data))
    title = screen.capitalize()
    name = out.get("name", title)
    out["name"] = name.replace(title, f"{title} v2", 1) if title in name else f"{name} v2"
    if isinstance(out.get("description"), str):
        out["description"] = out["description"].rstrip() + WORDING[screen]
    field = {"look": spec["look"], "moves": "switch.light_flows", "load": "switch.light_load"}
    if spec["colours"]:
        field["colours"] = spec["colours"]
    out["background"] = {"color": "#000000", "field": field}
    if isinstance(out.get("style"), dict):
        out["style"].update(FAMILIES[f"{screen}-v2"].cards)
    out["widgets"] = [w for w in out.get("widgets", []) if w.get("id") not in spec["without"]]
    out["switches"] = {**(out.get("switches") or {}), **json.loads(json.dumps(SWITCHES))}
    return out


def text(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the v2 screens")
    args = parser.parse_args(argv)
    differs = []
    for first, v2 in versions():
        screen = VERSION.match(first)["screen"]  # type: ignore[index]
        source = THEMES / first / "theme.json"
        target = THEMES / v2 / "theme.json"
        want = text(made(screen, json.loads(source.read_text(encoding="utf-8"))))
        have = target.read_text(encoding="utf-8") if target.is_file() else None
        if have != want:
            differs.append(f"{v2}: not as {first} makes it")
            if args.write:
                target.parent.mkdir(exist_ok=True)
                target.write_text(want, encoding="utf-8")
    for line in differs:
        print(line)
    if args.write:
        print(f"{len(differs)} written")
        return 0
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
