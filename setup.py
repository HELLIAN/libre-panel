"""Libre Panel's files (the ``data`` folder: themes, fonts, translations, pictures, the app's
pages) go into an install's ``share/libre-panel``, where both apps look for them
(src/libre_panel/datadir.py, rust/app/src/home.rs). The rest of the build is pyproject.toml's.
"""

from pathlib import Path

from setuptools import setup

DATA = Path("data")


def data_files() -> list[tuple[str, list[str]]]:
    """Each folder of ``data`` with its files, under ``share/libre-panel``."""
    folders: dict[str, list[str]] = {}
    for path in sorted(DATA.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            target = Path("share", "libre-panel", path.parent.relative_to(DATA)).as_posix()
            folders.setdefault(target, []).append(path.as_posix())
    return sorted(folders.items())


setup(data_files=data_files())
