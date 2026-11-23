"""packaging/check_tools.py, which CI runs on the program folder, on what the setup installed and
on the AppImage's content: it must pass a program that does the work, and say what is wrong with
one that does not. The programs are stand-ins here; one test runs this system's ffmpeg with the
program of this checkout, one the engine."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_tools", ROOT / "packaging" / "check_tools.py")
check_tools = importlib.util.module_from_spec(spec)
sys.modules["check_tools"] = check_tools
spec.loader.exec_module(check_tools)

START = b"\x00\x00\x00\x01"
SPS, PPS, IDR, SLICE = (
    START + b"\x67\x42\x00",
    START + b"\x68\xce\x3c",
    START + b"\x65\x88\x84",
    START + b"\x41\x9a\x24",
)
ENCODERS = (
    " V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC\n V....D libx264rgb  RGB\n"
)
CLIP = 44  # the pictures of a standby clip


def stream(pictures=check_tools.FRAMES):
    """What x264 writes: parameter sets and a keyframe, then one slice per picture."""
    return SPS + PPS + IDR + SLICE * (pictures - 1)


def stand_in(
    tools=None, encoders=ENCODERS, clip=None, told=None, found=None, read=None, code=0,
    version="ffmpeg version 8.1.3",
):  # fmt: skip
    """A ``run`` that answers as ffmpeg and the program do to what the check asks: ffmpeg's
    version and encoders, where the program finds its programs (``found``: those of
    ``tools``), the standby clip it makes (``clip``, ``told``, ``code``) and the pictures
    ffmpeg reads of it (``read``)."""

    def run(command, **options):
        name = Path(command[0]).name
        if name.startswith("libre-panel"):
            assert "LIBRE_PANEL_ENGINE" not in options["env"]  # (nothing of this computer's)
            assert Path(options["env"]["LIBRE_PANEL_HOME"]).is_dir()
            if command[1:] == ["tools"]:
                where = found if found is not None else {
                    check_tools.ASKED[f.name]: str(f) for f in tools.iterdir()
                    if f.name in check_tools.ASKED
                }  # fmt: skip
                return subprocess.CompletedProcess(command, 0, json.dumps(where), "")
            assert command[1:3] == ["tools", "clip"]
            if code:
                return subprocess.CompletedProcess(command, code, "", "no ffmpeg found")
            Path(command[3]).write_bytes(stream(CLIP) if clip is None else clip)
            ffmpeg = next(f for f in tools.iterdir() if f.name.startswith("ffmpeg"))
            said = {"model": "turing-8.8-usb", "ffmpeg": str(ffmpeg), "pictures": CLIP}
            return subprocess.CompletedProcess(command, 0, json.dumps({**said, **(told or {})}), "")
        if "-encoders" in command:
            return subprocess.CompletedProcess(command, 0, encoders, "")
        if "-version" in command:
            out = f"{version} Copyright (c) the FFmpeg developers\nbuilt with gcc\n"
            return subprocess.CompletedProcess(command, 0, out, "")
        assert command[-3:] == ["-f", "framecrc", "-"]
        lines = "#tb 0: 1/25\n" + "0, 0, 0, 1, 1, 0x0\n" * (CLIP if read is None else read)
        return subprocess.CompletedProcess(command, 0, lines, "")

    return run


def program_folder(tmp_path, files, executable="libre-panel"):
    """tmp_path/program with the program of Libre Panel and its tools folder holding the
    files, and a SOURCES.txt that lists them."""
    tools = tmp_path / "program" / "tools"
    tools.mkdir(parents=True)
    (tmp_path / "program" / executable).write_bytes(b"program")
    for name, content in files.items():
        (tools / name).write_bytes(content)
    sums = "\n".join(f"{hashlib.sha256(c).hexdigest()}  {n}" for n, c in files.items())
    (tools / "SOURCES.txt").write_text(
        f"Programs.\n\nSHA-256 of the files in this folder:\n{sums}\n"
    )
    return tmp_path / "program"


def test_what_the_check_asks_is_what_the_program_says():
    """The folder the program looks in, the names `libre-panel tools` gives its programs, and
    what the video mode tells the engine, as rust/ has them."""
    h264 = (ROOT / "rust/devices/src/h264.rs").read_text()
    assert f'.join("{check_tools.TOOLS_FOLDER}")' in h264
    said = (ROOT / "rust/app/src/tools.rs").read_text()
    assert set(check_tools.ASKED.values()) == set(re.findall(r'told.insert\(\s*"(\w+)"', said)) - {
        "model", "pictures", "bytes",
    }  # fmt: skip
    turzx = (ROOT / "rust/devices/src/turzx.rs").read_text()
    assert f'pub const DRAWN_X264: &str = "{check_tools.DRAWN_X264}";' in turzx


def test_a_good_folder_passes(tmp_path, capsys):
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    check_tools.check(program, stand_in(program / "tools"))
    out = capsys.readouterr().out
    assert "ffmpeg is the file that was fetched" in out
    assert "started from libre-panel, the app finds ffmpeg in tools" in out
    assert "ffmpeg version 8.1.3 Copyright (c) the FFmpeg developers lists libx264" in out
    assert f"makes the standby clip ({CLIP} pictures" in out and "reads it back" in out


def test_the_standby_clip_is_the_one_the_program_makes(tmp_path):
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    asked = []

    def run(command, **options):
        asked.append(command)
        return stand_in(program / "tools")(command, **options)

    check_tools.check(program, run)
    clip = next(command for command in asked if command[1:3] == ["tools", "clip"])
    assert clip[0] == str(program / "libre-panel") and clip[3].endswith("standby.h264")
    read = asked[-1]
    assert read[0] == str(program / "tools" / "ffmpeg") and read[read.index("-i") + 1] == clip[3]


@pytest.mark.parametrize(
    "encoders",
    ["", " V....D libx264rgb  RGB\n", " A....D aac AAC\n"],
    ids=["none", "rgb only", "audio"],
)
def test_an_ffmpeg_without_libx264_fails(tmp_path, encoders):
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    with pytest.raises(check_tools.CheckError, match="does not list libx264"):
        check_tools.check(program, stand_in(program / "tools", encoders=encoders))


@pytest.mark.parametrize(
    "answers, wanted",
    [
        ({"code": 1}, "tools clip .*: exit code 1 no ffmpeg found"),  # the program failed
        ({"clip": stream(3)}, "a standby clip of 3 pictures of 44"),  # pictures missing
        ({"clip": IDR + SLICE * 43}, "NAL units"),  # no parameter sets
        (
            {"clip": START + b"\x65\x08\x00" + START + b"\x61\x08\x00" + stream(CLIP)},
            "several slices: True",
        ),  # fmt: skip
        ({"told": {"ffmpeg": "/usr/bin/ffmpeg"}}, "made with /usr/bin/ffmpeg, not"),
        ({"read": 40}, "read 40 pictures of the 44 of the standby clip"),
    ],
)
def test_an_ffmpeg_that_does_not_make_the_standby_clip_fails(tmp_path, answers, wanted):
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    with pytest.raises(check_tools.CheckError, match=wanted):
        check_tools.check(program, stand_in(program / "tools", **answers))


@pytest.mark.parametrize(
    "shown, wanted",
    [("PresentMon 2.6.0\nPresentMon [options]\n", "PresentMon 2.6.0 starts"), ("usage", None)],
)
def test_presentmon_has_to_start_and_say_its_version(tmp_path, capsys, shown, wanted):
    def run(command, **options):
        assert command[1] == "--help"
        return subprocess.CompletedProcess(command, 1, "", shown)  # exit code 1 is how it ends

    if wanted:
        check_tools.check_presentmon(tmp_path / "PresentMon.exe", run)
        assert wanted in capsys.readouterr().out
    else:
        with pytest.raises(check_tools.CheckError, match="said no version"):
            check_tools.check_presentmon(tmp_path / "PresentMon.exe", run)


def test_a_windows_folder_checks_each_program_it_lists(tmp_path, capsys):
    program = program_folder(
        tmp_path,
        {"ffmpeg.exe": b"MZ1", "PresentMon.exe": b"MZ2", "PawnIO_setup.exe": b"MZ3"},
        executable="libre-panel.exe",
    )
    (program / "LibrePanel.exe").write_bytes(b"its window")  # (not asked: it answers nothing)
    seen = []

    def run(command, **options):
        seen.append(Path(command[0]).name)
        if Path(command[0]).name == "PresentMon.exe":
            return subprocess.CompletedProcess(command, 1, "", "PresentMon 2.6.0")
        return stand_in(program / "tools")(command, **options)

    check_tools.check(program, run)
    # (the installer is there, and not run)
    assert set(seen) == {"libre-panel.exe", "ffmpeg.exe", "PresentMon.exe"}
    out = capsys.readouterr().out
    assert "PawnIO_setup.exe is the file that was fetched" in out
    assert "started from libre-panel.exe, the app finds ffmpeg.exe in tools" in out
    assert "the app finds PresentMon.exe in tools" in out
    assert "the app finds PawnIO_setup.exe in tools" in out
    found = {"ffmpeg": str(program / "tools" / "ffmpeg.exe"), "pawnio": None}  # (not pinned)
    with pytest.raises(check_tools.CheckError, match="the app finds no PawnIO_setup.exe"):
        check_tools.check_discovery(
            program / "libre-panel.exe", program / "tools", ["ffmpeg.exe", "PawnIO_setup.exe"],
            stand_in(found=found),
        )  # fmt: skip


def test_the_app_has_to_find_the_programs_in_the_folder_and_not_somewhere_else(tmp_path):
    """A build whose tools folder is not where the app looks is a failure, even where a
    system ffmpeg would make the videos work."""
    program = program_folder(tmp_path, {"ffmpeg": b"x"})
    exe, tools = program / "libre-panel", program / "tools"
    elsewhere = stand_in(found={"ffmpeg": "/usr/bin/ffmpeg", "engine": None})
    with pytest.raises(check_tools.CheckError, match="finds /usr/bin/ffmpeg and not ffmpeg"):
        check_tools.check_discovery(exe, tools, ["ffmpeg"], elsewhere)
    with pytest.raises(check_tools.CheckError, match="the app finds no ffmpeg"):
        check_tools.check_discovery(exe, tools, ["ffmpeg"], stand_in(found={"ffmpeg": None}))
    exe.unlink()  # and without a program of Libre Panel there is no asking
    with pytest.raises(check_tools.CheckError, match="has no program of Libre Panel"):
        check_tools.check(program, stand_in(tools))


def test_a_file_that_is_not_the_fetched_one_or_is_gone_fails(tmp_path):
    program = program_folder(tmp_path, {"ffmpeg": b"x", "PresentMon.exe": b"MZ"})
    (program / "tools" / "ffmpeg").write_bytes(b"replaced")
    with pytest.raises(check_tools.CheckError, match="is not the file that was fetched"):
        check_tools.check(program, stand_in(program / "tools"))
    (program / "tools" / "ffmpeg").unlink()
    with pytest.raises(check_tools.CheckError, match="cannot be read"):
        check_tools.check(program, stand_in(program / "tools"))


def test_a_folder_without_tools_or_sources_or_ffmpeg_fails(tmp_path):
    with pytest.raises(check_tools.CheckError, match="has no tools folder"):
        check_tools.check(tmp_path, stand_in())
    (tmp_path / "tools").mkdir()
    with pytest.raises(check_tools.CheckError, match="SOURCES.txt cannot be read"):
        check_tools.check(tmp_path, stand_in())
    (tmp_path / "tools" / "SOURCES.txt").write_text("nothing listed\n")
    with pytest.raises(check_tools.CheckError, match="lists no file"):
        check_tools.check(tmp_path, stand_in())
    program = program_folder(tmp_path / "other", {"PresentMon.exe": b"MZ"})
    with pytest.raises(check_tools.CheckError, match="has no ffmpeg"):
        check_tools.check(program, stand_in(program / "tools"))


def test_the_command_says_ok_or_what_failed(tmp_path, capsys):
    assert check_tools.main([str(tmp_path)]) == 1
    assert "FAIL: " in capsys.readouterr().err
    program = program_folder(tmp_path / "good", {"ffmpeg": b"x"})
    assert check_tools.main([str(program)], stand_in(program / "tools")) == 0
    assert capsys.readouterr().out.endswith("OK\n")
    assert check_tools.main([str(program)], stand_in(program / "tools", encoders="")) == 1
    assert "does not list libx264" in capsys.readouterr().err


def test_a_program_that_cannot_be_started_is_a_failure(tmp_path):
    with pytest.raises(check_tools.CheckError, match="did not run"):
        check_tools.run([str(tmp_path / "missing-program")])


FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(FFMPEG is None or os.name == "nt", reason="needs ffmpeg (a Linux one here)")
def test_this_systems_ffmpeg_passes_with_the_program_of_this_checkout(tmp_path, capsys):
    """This system's ffmpeg in the tools of a folder with the program built in rust/ and the
    themes and fonts of this checkout: found there, the standby clip made and read back."""
    from rust_check import find

    encoders = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    if "libx264 " not in encoders.stdout:
        pytest.skip("this ffmpeg has no libx264")
    built = (find() or ROOT / "rust/target/release/lp-check").with_name("libre-panel")
    if not built.is_file():
        pytest.skip("needs the libre-panel program (rust/ built)")
    program = program_folder(tmp_path, {"ffmpeg": Path(FFMPEG).read_bytes()})
    (program / "tools" / "ffmpeg").chmod(0o755)
    shutil.copy2(built, program / "libre-panel")
    (program / "share").mkdir()
    (program / "share" / "libre-panel").symlink_to(ROOT / "data")
    check_tools.check(program)
    out = capsys.readouterr().out
    assert "the app finds ffmpeg in tools" in out and "reads it back" in out


# -- Libre Panel's own engine (packaging/add_engine.py puts it in) ----------------------------


def load_add_engine():
    spec = importlib.util.spec_from_file_location(
        "add_engine", ROOT / "packaging" / "add_engine.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_engine_goes_in_said_in_sources_and_checked_as_the_others(tmp_path):
    add_engine = load_add_engine()
    tools = tmp_path / "bundled-tools"
    (tools / "licenses").mkdir(parents=True)
    ffmpeg = b"\x7fELF ffmpeg"
    (tools / "ffmpeg").write_bytes(ffmpeg)
    (tools / "SOURCES.txt").write_text(
        "Programs that come with Libre Panel, in this folder (see also NOTICE.md).\n\nffmpeg\n"
        "  FFmpeg ...\n\nSHA-256 of the files in this folder:\n"
        f"{hashlib.sha256(ffmpeg).hexdigest()}  ffmpeg\n"
    )
    built = tmp_path / "target" / "libre-panel-engine"
    built.parent.mkdir()
    program = b"\x7fELF engine"
    built.write_bytes(program)
    line = add_engine.add(
        tools, built, "0.164.3108", ["x264: https://code.videolan.org/videolan/x264"]
    )
    assert line == f"{hashlib.sha256(program).hexdigest()}  libre-panel-engine"
    text = (tools / "SOURCES.txt").read_text()
    assert text.index("libre-panel-engine\n  Libre Panel's own engine") < text.index(
        "SHA-256 of the"
    )
    assert "libx264 0.164.3108 linked in" in text and "code.videolan.org/videolan/x264" in text
    assert (
        (tools / "licenses" / "x264-COPYING.txt")
        .read_text()
        .startswith(
            "                    GNU GENERAL PUBLIC LICENSE\n                       Version 2"
        )
    )
    assert check_tools.check_files(tools) == ["ffmpeg", "libre-panel-engine"]  # (both checked)
    with pytest.raises(add_engine.AddError):  # a Windows program is no Linux one
        built.write_bytes(b"MZ engine")
        add_engine.add(tools, built, "0.164", ["x264"])


@pytest.mark.skipif(shutil.which("cargo") is None, reason="needs cargo")
def test_the_crates_in_the_engine_go_with_their_licences(tmp_path):
    """What cargo builds into the engine for the target (not what only its tests or its build
    need), each crate with its licence files' text; into ``licenses`` with the engine."""
    add_engine = load_add_engine()
    for target, own in (("x86_64-pc-windows-gnu", "windows-sys"),
                        ("x86_64-unknown-linux-gnu", "rustix")):  # fmt: skip
        text = add_engine.crate_notices(target=target)
        names = [line.split()[0] for line in text.splitlines()
                 if line and line[0].isalpha() and line.split()[-1][:1].isdigit()]  # fmt: skip
        assert {"nusb", "des", "cbc", own} <= set(names)
        assert "libre-panel-engine" not in names  # (its own licence is the program's)
        assert "Permission is hereby granted" in text  # (MIT's words, from the crates' files)
    windows = add_engine.crate_notices(target="x86_64-pc-windows-gnu")
    assert "\nrustix " not in windows and "\nlibc " not in windows  # (Linux's own, not there)
    tools = tmp_path / "tools"
    (tools / "licenses").mkdir(parents=True)
    (tools / "SOURCES.txt").write_text("x\n\nSHA-256 of the files in this folder:\n")
    built = tmp_path / "libre-panel-engine"
    built.write_bytes(b"\x7fELF engine")
    add_engine.add(tools, built, "0.164", ["x264"], windows)
    assert (tools / "licenses" / add_engine.CRATES).read_text("utf-8") == windows
    assert f"licenses/{add_engine.CRATES}" in (tools / "SOURCES.txt").read_text("utf-8")


def test_an_engine_that_does_not_encode_the_stream_fails(tmp_path):
    def run(command, **options):
        if command[1:] == ["version"]:
            return subprocess.CompletedProcess(
                command, 0, "libre-panel-engine 0.1.0 (x264 build 164)\n", ""
            )
        assert options["input"].startswith(b"LPE1"), "the engine's records should be fed in"
        return subprocess.CompletedProcess(command, 0, stream(3), b"")

    with pytest.raises(check_tools.CheckError, match="3 pictures of 8 frames"):
        check_tools.check_engine(tmp_path / "libre-panel-engine", run)


def test_the_engine_of_this_checkout_passes_when_it_is_built(capsys):
    from test_rust_app import _engine

    engine = _engine()
    if not engine:
        pytest.skip("needs libre-panel-engine (cargo build --release in engine/)")
    check_tools.check_engine(Path(engine))
    out = capsys.readouterr().out
    assert "x264 build" in out and "encodes 8 frames" in out
