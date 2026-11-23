"""The themes the clips of tools/readme_media.py are made of: built-in ones, ones in a folder, or
screens made of modules in a look of the editor (:data:`LAYOUTS`); and their grids, as Libre Panel
makes them (lp-check, tools/lp_check.py). Nothing here runs the app."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import readme_images as images
from lp_check import ask

ROOT = Path(__file__).resolve().parent.parent
THEME_FORMAT = "libre-panel-theme/1"

# Themes a recipe may name besides the built-in ones: screens that Libre Panel's themes do not
# have, made of modules in Libre Panel's own look (tools/media_app.py adds its own).
# ``--theme clip=THEME`` puts any theme in a clip's place.
LAYOUTS: dict[str, Callable[[], dict[str, Any]]] = {}


def module(kind: str, col: int, row: int, cols: int = 1, rows: int = 1, **options: Any) -> dict:
    return {"module": kind, "col": col, "row": row, "cols": cols, "rows": rows, **options}


def modules_theme(
    look: str,
    modules: list[dict],
    refresh_ms: int = 500,
    model: str = "turing-9.2-usb",
) -> dict[str, Any]:
    """A theme made of modules for a panel (the 9.2" bar unless ``model`` says another), in one
    of the looks of the editor (the pictures of tools/readme_images.py are made the same way)."""
    chosen = images.parts()["looks"][look]
    palette, style = chosen["palette"], chosen["style"]
    data: dict[str, Any] = {
        "format": THEME_FORMAT,
        "name": f"Clip layout ({look})",
        "display": {"model": model, "orientation": "landscape"},
        "palette": palette,
        "style": style,
        "background": {"color": palette["bg"]},
        "refresh_ms": refresh_ms,
        "animation": {"smoothing_ms": 500},
        "widgets": [{"type": "module", "id": f"m{n}", **m} for n, m in enumerate(modules)],
    }
    return data


def theme_folder(source: str) -> Path | None:
    """``source`` as a folder with a theme in it (``theme.json``), if it is one."""
    folder = Path(source)
    return folder if (folder / "theme.json").is_file() else None


def find_theme(theme_id: str) -> Path:
    """The folder of the built-in theme ``theme_id``."""
    folder = ROOT / "data" / "themes" / theme_id
    if not (folder / "theme.json").is_file():
        raise FileNotFoundError(f"theme not found: {theme_id}")
    return folder


def theme_data(source: str | dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    """The data of a theme, and the folder its files are in (None: it has none). ``source`` is
    theme data, one of the :data:`LAYOUTS`, a folder with a theme in it, or the id of a built-in
    theme."""
    if isinstance(source, dict):
        return dict(source), None
    if source in LAYOUTS:
        return LAYOUTS[source](), None
    folder = theme_folder(source) or find_theme(source)
    return json.loads((folder / "theme.json").read_text(encoding="utf-8")), folder


class Grid:
    """A theme's size and grid, as Libre Panel makes them (lp-check's theme_grid)."""

    def __init__(self, data: dict[str, Any]) -> None:
        (said,) = ask([{"op": "theme_grid", "theme": json.dumps(data),
                        "fonts_dir": str(ROOT / "data" / "fonts")}])  # fmt: skip
        if "error" in said:
            raise ValueError(f"no theme: {said['error']}")
        self.width, self.height, self.model = said["width"], said["height"], said["model"]
        self.x, self.y, self.gap = said["x"], said["y"], said["gap"]
        self.cell_w, self.cell_h = said["cell_w"], said["cell_h"]
        self.columns, self.rows = said["columns"], said["rows"]

    def box(self, col: int, row: int, cols: int, rows: int) -> list[int]:
        """(x, y, width, height) of the cells from ``col``, ``row`` (theme/modules.py's)."""
        x0 = self.x + col * (self.cell_w + self.gap)
        y0 = self.y + row * (self.cell_h + self.gap)
        x1 = x0 + cols * self.cell_w + (cols - 1) * self.gap
        y1 = y0 + rows * self.cell_h + (rows - 1) * self.gap
        return [round(x0), round(y0), round(x1) - round(x0), round(y1) - round(y0)]
