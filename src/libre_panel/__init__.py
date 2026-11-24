"""Libre Panel: free software for USB system-monitor displays."""

import os
import re
import time
from pathlib import Path

# numpy's products of small matrices (a plugin's) in the thread that asks for them: OpenBLAS
# would spread each over every core, and while a game keeps them busy its threads wait for one
# another (in the Python app a blur of a tenth of a millisecond took twenty). Set before numpy is
# loaded; one set already holds.
for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "1")
del _name


def _version() -> str:
    """Libre Panel's version, which pyproject.toml alone sets (docs/RELEASING.md): read there in
    the source tree, else from what the install wrote of it."""
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        found = re.search(r'^name = "libre-panel"\n(?:.*\n)*?version = "([^"]+)"',
                          project.read_text(encoding="utf-8"), re.M)  # fmt: skip
    except OSError:
        found = None
    if found:
        return found[1]
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("libre-panel")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _version()


# where clock() counts from on time.perf_counter's: the app's (count_from)
_zero = 0.0


def clock() -> float:
    """The main loop's clock, in seconds: the ``now`` a plugin's screen or widget type is drawn
    with is a time on it (the time the frame is planned for), and so are the time stamps of
    states. It is ``time.perf_counter``, fine-grained on every system, counted in the plugin
    host from where the app's clock began; compare with it, not with ``time.monotonic``,
    which is coarse on Windows before Python 3.13."""
    return time.perf_counter() - _zero


def count_from(zero: float) -> None:
    """``clock()`` from now on counts from ``zero`` on time.perf_counter's: where the app's
    clock began, which the app tells the plugin host it starts (plugin_host.py)."""
    global _zero
    _zero = zero
