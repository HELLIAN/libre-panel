"""The programs that come with a download, in a ``tools`` folder next to Libre Panel, and the
libraries of the system's programs a Linux download starts.

The Windows setup and zip carry ffmpeg (the video mode), PresentMon (game frame rates) and
PawnIO's installer (the CPU's sensors); the Linux AppImage and tarball carry ffmpeg.
``packaging/fetch_tools.py`` fetches them when a download is built, ``tools/SOURCES.txt`` and
NOTICE.md say where they come from; the Rust app finds them itself. Of the plugin host, which a
download carries too, rights.py takes the folder as one of those a program it may start lies in.

Only a release build looks here, and only beside its own program: whoever may write there
may replace Libre Panel itself. Never the user's Downloads.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

TOOLS_FOLDER = "tools"  # in the downloads, next to the program's own files


def tools_folders() -> list[Path]:
    """The folders a download keeps its programs in, the one beside the program first. None
    for a pip install or a checkout, which carry no programs."""
    if not getattr(sys, "frozen", False):
        return []
    folders = [Path(sys.executable).resolve().parent / TOOLS_FOLDER]
    bundle = getattr(sys, "_MEIPASS", None)  # PyInstaller's own folder (_internal)
    if bundle and (inside := Path(bundle) / TOOLS_FOLDER) not in folders:
        folders.append(inside)
    return folders


def system_libraries(
    environ: MutableMapping[str, str] = os.environ,
    frozen: bool | None = None,
    platform: str = sys.platform,
) -> None:
    """In a Linux download, the system's programs that Libre Panel starts (the browser through
    xdg-open, playerctl, pactl, notify-send) get the system's own libraries. PyInstaller's
    loader puts the bundle's folder first in LD_LIBRARY_PATH for itself, the path from before
    kept in LD_LIBRARY_PATH_ORIG; inherited, it made the system's /bin/sh take the bundle's
    older readline and stop at once ("undefined symbol: rl_trim_arg_from_keyseq"), so xdg-open
    never opened the app (#21). This process keeps the libraries it has (the loader reads the
    path when a program starts), and a program of the bundle that it starts sets the path again
    for itself."""
    if not (getattr(sys, "frozen", False) if frozen is None else frozen):
        return
    if not platform.startswith("linux") or "LD_LIBRARY_PATH" not in environ:
        return
    original = environ.get("LD_LIBRARY_PATH_ORIG")
    if original:
        environ["LD_LIBRARY_PATH"] = original
    else:
        del environ["LD_LIBRARY_PATH"]
