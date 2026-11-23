"""What bundled.py knows of the programs a download carries (ffmpeg, PresentMon, PawnIO's
installer): their names on each system, PawnIO's installer only as it was fetched, and the
libraries a Linux download's programs get."""

import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from libre_panel import bundled


def exe(platform):
    return ".exe" if platform == "win32" else ""


def release_build(tmp_path, monkeypatch, platform="linux", *files, folder="tools"):
    """A frozen build whose program folder holds ``files`` in ``folder`` (the tools folder)."""
    program = tmp_path / "program"
    (program / folder).mkdir(parents=True, exist_ok=True)
    for name in files:
        (program / folder / name).write_bytes(b"x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program / f"libre-panel{exe(platform)}"))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return program


@pytest.fixture(autouse=True)
def nothing_on_the_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


def test_the_file_names_follow_the_system(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert bundled.program_name("ffmpeg") == "ffmpeg.exe"
    monkeypatch.setattr(sys, "platform", "linux")
    assert bundled.program_name("ffmpeg") == "ffmpeg"


def test_pawnios_installer_is_handed_out_only_as_it_was_fetched(tmp_path, monkeypatch):
    """It is run with administrator rights from a folder the installing user may write to."""
    program = release_build(tmp_path, monkeypatch, "win32")
    assert bundled.pawnio_installer() is None  # not carried
    setup = program / "tools" / bundled.PAWNIO_SETUP
    setup.write_bytes(b"MZ another program")
    assert bundled.pawnio_installer() is None  # replaced
    setup.unlink()
    setup.mkdir()
    assert bundled.pawnio_installer() is None  # not a file
    setup.rmdir()
    monkeypatch.setattr(
        bundled, "PAWNIO_SETUP_SHA256", hashlib.sha256(b"MZ the real one").hexdigest()
    )
    setup.write_bytes(b"MZ the real one")
    assert bundled.pawnio_installer() == str(setup)
    monkeypatch.delattr(sys, "frozen")
    assert bundled.pawnio_installer() is None  # a pip install carries none


def test_the_pin_the_app_checks_is_the_one_the_download_was_fetched_by():
    spec = importlib.util.spec_from_file_location(
        "fetch_tools_pin", Path(__file__).resolve().parent.parent / "packaging" / "fetch_tools.py"
    )
    fetch_tools = importlib.util.module_from_spec(spec)
    sys.modules["fetch_tools_pin"] = fetch_tools
    spec.loader.exec_module(fetch_tools)
    pawnio = [tool for tool in fetch_tools.TOOLS if tool.file == bundled.PAWNIO_SETUP]
    assert [tool.sha256 for tool in pawnio] == [bundled.PAWNIO_SETUP_SHA256]


def test_the_system_s_programs_get_the_system_s_libraries_in_a_linux_download():
    """The AppImage's loader put its folder first in LD_LIBRARY_PATH; inherited, it made the
    system's /bin/sh take the bundle's older readline and stop, so the app never opened (#21).
    The programs it starts get the path from before, or none."""
    env = {"LD_LIBRARY_PATH": "/tmp/_MEI/lib:/opt/mine", "LD_LIBRARY_PATH_ORIG": "/opt/mine",
           "PATH": "/usr/bin"}  # fmt: skip
    bundled.system_libraries(env, frozen=True, platform="linux")
    assert env == {"LD_LIBRARY_PATH": "/opt/mine", "LD_LIBRARY_PATH_ORIG": "/opt/mine",
                   "PATH": "/usr/bin"}  # fmt: skip
    env = {"LD_LIBRARY_PATH": "/tmp/_MEI/lib", "PATH": "/usr/bin"}  # none from before
    bundled.system_libraries(env, frozen=True, platform="linux")
    assert env == {"PATH": "/usr/bin"}
    for frozen, platform in ((False, "linux"), (True, "win32")):  # a checkout, Windows: as is
        env = {"LD_LIBRARY_PATH": "/tmp/_MEI/lib"}
        bundled.system_libraries(env, frozen=frozen, platform=platform)
        assert env == {"LD_LIBRARY_PATH": "/tmp/_MEI/lib"}
    env = {}
    bundled.system_libraries(env, frozen=True, platform="linux")
    assert env == {}
