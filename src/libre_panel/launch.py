"""``libre-panel`` of a pip or pipx install on Windows: the Rust app with the arguments given.

The Windows wheel (packaging/platform_wheel.py) puts the app as its download lays it out (the
programs with their DLLs, the engine in ``tools``, the sensor service in ``sensors``) into
``libre-panel`` of the install, beside its ``share/libre-panel``. Its command is this one, not
the program itself: pipx copies a command it cannot link (Windows gives no symbolic links
without developer mode) into a folder of its own, where the program would have none of its
files; a Python command finds its environment wherever it is copied. The app is run, not
replaced, as Windows has no exec: this process waits for it and leaves with its exit code, and
leaves Ctrl+C to it. What the app sets up to start by itself (at login, from the start menu)
starts the program itself. On Linux the programs are the commands.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

FOLDER = "libre-panel"
PROGRAM = "libre-panel.exe" if os.name == "nt" else "libre-panel"


def program() -> Path:
    """The app's program: in ``libre-panel`` of the environment, else where the install put
    it (a user's install puts it elsewhere), as the package's own list of files says."""
    beside = Path(sys.prefix) / FOLDER / PROGRAM
    if beside.is_file():
        return beside
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        files = distribution("libre-panel").files or []
    except PackageNotFoundError:
        files = []
    for file in files:
        if file.name == PROGRAM and file.parent.name == FOLDER:
            return Path(str(file.locate()))
    raise SystemExit(f"libre-panel: the app's program is not here ({beside})")


def main() -> int:
    app = program()
    arguments = [str(app), *sys.argv[1:]]
    if os.name != "nt":
        os.execv(app, arguments)
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)  # (the app has it and ends itself)
    return subprocess.call(arguments)


if __name__ == "__main__":
    sys.exit(main())
