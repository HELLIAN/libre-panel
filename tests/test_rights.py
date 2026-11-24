"""With the highest rights Libre Panel loads and starts nothing that programs without them may
change (rights.py): which places are safe, which programs are found there, and who has those
rights."""

import os
import sys
from pathlib import Path

import pytest

from libre_panel import rights


@pytest.fixture
def elevated(monkeypatch):
    """Libre Panel runs with the highest rights; a place is safe in a folder named "safe"."""
    monkeypatch.setattr(rights, "elevated", lambda: True)
    monkeypatch.setattr(rights, "safe_place", lambda path: "safe" in Path(path).parts)


def program(folder, name):
    """An executable file ``name`` in ``folder``, as PATH finds it."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{name}.exe" if sys.platform == "win32" else name)
    path.write_text("")
    path.chmod(0o755)
    return path


def found(path):
    """A path found, as the file system compares it (Windows' PATH finds "ffmpeg.EXE")."""
    return os.path.normcase(path) if path else path


def test_without_the_highest_rights_every_place_will_do(tmp_path, monkeypatch):
    monkeypatch.setattr(rights, "elevated", lambda: False)
    assert rights.safe_place(tmp_path / "anything")
    tool = program(tmp_path / "bin", "lp-tool")
    monkeypatch.setenv("PATH", str(tool.parent))
    assert found(rights.which("lp-tool")) == found(str(tool))


def test_on_windows_only_the_folders_of_administrators_are_safe(tmp_path):
    protected = [tmp_path / "Program Files", tmp_path / "Windows"]
    for folder in protected:
        folder.mkdir()
    assert rights._safe(tmp_path / "Program Files" / "ffmpeg" / "ffmpeg.exe", True, protected)
    assert rights._safe(tmp_path / "Windows" / "System32" / "x.exe", True, protected)
    assert not rights._safe(tmp_path / "Users" / "me" / "ffmpeg.exe", True, protected)
    # a way out of a protected folder is followed to where it leads
    assert not rights._safe(tmp_path / "Program Files" / ".." / "Users" / "x.exe", True, protected)
    assert not rights._safe(tmp_path / "Program Files Evil" / "x.exe", True, protected)


@pytest.mark.skipif(sys.platform == "win32", reason="owners and modes are POSIX'")
def test_as_root_a_place_is_safe_where_nobody_else_may_write_above_it(tmp_path):
    owners = (0, os.getuid())  # (the tests' own user stands in for root)
    folder = tmp_path / "bin"
    folder.mkdir(mode=0o755)
    tool = program(folder, "ffmpeg")
    assert rights._safe(tool, False, owners=owners)
    tool.chmod(0o777)  # anyone may change the program
    assert not rights._safe(tool, False, owners=owners)
    tool.chmod(0o755)
    folder.chmod(0o777)  # anyone may replace it
    assert not rights._safe(tool, False, owners=owners)
    folder.chmod(0o1777)  # unless the sticky bit keeps what is not theirs (as /tmp)
    assert rights._safe(tool, False, owners=owners)
    folder.chmod(0o755)
    assert not rights._safe(tool, False, owners=(os.getuid() + 1,))  # someone else's


def test_with_the_highest_rights_only_a_program_from_a_safe_place_is_found(
    tmp_path, monkeypatch, elevated
):
    unsafe = program(tmp_path / "user" / "bin", "lp-tool")
    monkeypatch.setenv("PATH", str(unsafe.parent))
    assert rights.which("lp-tool") is None
    safe = program(tmp_path / "safe" / "bin", "lp-tool")
    monkeypatch.setenv("PATH", os.pathsep.join([str(unsafe.parent), str(safe.parent)]))
    assert found(rights.which("lp-tool")) == found(str(safe))  # the first one that may be started


# -- who has the highest rights -------------------------------------------------------------------


real_elevated = rights.elevated  # (conftest.py replaces it for the tests; this is the real one)


def test_root_and_an_administrator_are_elevated(monkeypatch):
    monkeypatch.setattr(rights.sys, "platform", "linux")
    monkeypatch.setattr(rights.os, "geteuid", lambda: 0, raising=False)
    assert real_elevated() is True
    monkeypatch.setattr(rights.os, "geteuid", lambda: 1000, raising=False)
    assert real_elevated() is False
    monkeypatch.delattr(rights.os, "geteuid", raising=False)  # no such thing there
    assert real_elevated() is False

    monkeypatch.setattr(rights.sys, "platform", "win32")
    for answer in (True, False):
        monkeypatch.setattr(rights, "is_admin", lambda answer=answer: answer)
        assert real_elevated() is answer
