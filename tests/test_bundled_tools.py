"""What bundled.py knows of the programs a download carries: the libraries a Linux download's
programs get (the plugin host sets them first), and where its tools are."""

import shutil
import sys

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


def test_the_plugin_host_gives_the_programs_it_starts_the_system_s_libraries(monkeypatch):
    """A program a plugin starts in a Linux download (notify-send, xdg-open) gets the system's
    libraries, as it did in the Python app: the plugin host sets them back before anything."""
    import io

    from libre_panel import plugin_host

    called = []
    monkeypatch.setattr(bundled, "system_libraries", lambda: called.append(True))
    empty = type("Stream", (), {"buffer": io.BytesIO()})()
    monkeypatch.setattr(sys, "stdin", empty)
    monkeypatch.setattr(sys, "stdout", type("Stream", (), {"buffer": io.BytesIO()})())
    monkeypatch.setattr(
        plugin_host,
        "Host",
        lambda channel, folder: type("Host", (), {"serve": lambda self: called.append("served")})(),
    )
    assert plugin_host.main([]) == 0
    assert called == [True, "served"]


def test_a_download_s_tools_are_beside_its_program_and_in_its_bundle(tmp_path, monkeypatch):
    """Where a download keeps its programs (rights.py trusts them): beside its program, then in
    PyInstaller's own folder; none for a pip install or a checkout."""
    program = release_build(tmp_path, monkeypatch)
    assert bundled.tools_folders() == [program.resolve() / "tools"]
    monkeypatch.setattr(sys, "_MEIPASS", str(program / "_internal"), raising=False)
    assert bundled.tools_folders() == [program.resolve() / "tools", program / "_internal" / "tools"]
    monkeypatch.setattr(sys, "frozen", False)
    assert bundled.tools_folders() == []
