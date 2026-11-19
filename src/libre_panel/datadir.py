"""Where Libre Panel's own files are: the built-in themes, the fonts, the translations, the
pictures and the app's pages. They are no part of the Python package: the repository keeps
them in its ``data`` folder, which the Rust app reads too (rust/app/src/home.rs), and an
install puts them in ``share/libre-panel``.

Looked for in this order: the folder LIBRE_PANEL_DATA names; the ``data`` folder of the
source tree this package is in; ``share/libre-panel`` of the Python environment and of the
user's (where a pip or pipx install puts them); for a built program, its own copy, else
``share/libre-panel`` beside it or beside its folder, as the Rust app looks for it (the
downloads' plugin host, in "plugin-host" beside the app).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SHARE = Path("share") / "libre-panel"


def holds_data(folder: Path) -> bool:
    """Whether ``folder`` is such a folder (the test rust/app/src/home.rs makes)."""
    return (folder / "themes").is_dir() and (folder / "fonts").is_dir()


def find() -> Path:
    """The folder of Libre Panel's files; where none is found, the environment's, where an
    install would have put them (what fails to be read there names it)."""
    named = os.environ.get("LIBRE_PANEL_DATA")
    if named:
        return Path(named)
    tried = [Path(__file__).resolve().parents[2] / "data", Path(sys.prefix) / SHARE]
    if getattr(sys, "frozen", False):
        program = Path(sys.executable).resolve().parent
        tried += [Path(getattr(sys, "_MEIPASS", program)) / SHARE, program / SHARE,
                  program.parent / SHARE]  # fmt: skip
    else:
        import site

        tried.append(Path(site.getuserbase()) / SHARE)
    return next((folder for folder in tried if holds_data(folder)), tried[1])


DATA = find()
