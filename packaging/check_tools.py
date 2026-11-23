"""Check the programs a download carries, where they are: the folder a setup installed, the
folder inside an unpacked AppImage, the program folder CI built.

    python packaging/check_tools.py PROGRAM_FOLDER

PROGRAM_FOLDER is the one with ``libre-panel`` in it; its ``tools`` folder is checked:

  * every file that ``tools/SOURCES.txt`` lists is there and is the one that was fetched
    (its SHA-256);
  * the program finds them there (``libre-panel tools``: it looks for them as it does when it
    runs), and not what the PATH holds; PawnIO's installer only as the file it has pinned;
  * ffmpeg lists libx264 among its encoders, makes the standby clip of a TURZX panel the way
    the program makes it (``libre-panel tools clip``: its own command), a stream of as many
    pictures as went in, each one slice, with the parameter sets the panel needs, and reads
    it back as the program reads videos;
  * Libre Panel's own engine, where the build put it in (packaging/add_engine.py), says its
    version and the x264 it carries, and encodes frames as the video mode feeds it, to a stream
    of as many pictures;
  * PresentMon (Windows) starts and says its version;
  * PawnIO's installer (Windows) is there. It is not run: it installs a driver.

Needs Python and nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

TOOLS_FOLDER = "tools"  # (where the program looks: rust/devices/src/h264.rs find_ffmpeg)
LISTED = re.compile(r"^([0-9a-f]{64})  (\S+)$", re.MULTILINE)
FRAMES = 8
SIZE = (192, 48)  # small, and even: the video mode turns the bars' 1920x480 the same way
# the programs of Libre Panel that answer on the command line (LibrePanel.exe is its window)
PROGRAMS = ("libre-panel.exe", "libre-panel")
# what `libre-panel tools` calls each program the download carries
ASKED = {
    "ffmpeg": "ffmpeg",
    "ffmpeg.exe": "ffmpeg",
    "libre-panel-engine": "engine",
    "libre-panel-engine.exe": "engine",
    "PresentMon.exe": "presentmon",
    "PawnIO_setup.exe": "pawnio",
}
# what the video mode tells the engine for drawn frames (rust/devices/src/turzx.rs DRAWN_X264,
# engine_options with the default [video])
DRAWN_X264 = "no-deblock=1:subme=0:deadzone-inter=6:deadzone-intra=6"
ENGINE_OPTIONS = ["--turn", "270", "--fps", "25", "--crf", "20", "--kbit", "8000",
                  "--keyint", "250", "--preset", "superfast", "--profile", "high",
                  "--x264", DRAWN_X264]  # fmt: skip
NAL_SLICE, NAL_IDR, NAL_SPS, NAL_PPS = 1, 5, 7, 8


class CheckError(Exception):
    """A program is missing or does not do what it should; the message says what."""


def listed_files(tools: Path) -> dict[str, str]:
    """The SHA-256 of each file that SOURCES.txt lists (file name -> hash)."""
    sources = tools / "SOURCES.txt"
    try:
        text = sources.read_text("utf-8")
    except OSError as exc:
        raise CheckError(f"{sources} cannot be read: {exc}") from exc
    files = {name: digest for digest, name in LISTED.findall(text)}
    if not files:
        raise CheckError(f"{sources} lists no file with its SHA-256")
    return files


def check_files(tools: Path) -> list[str]:
    """Every listed file is there as it was fetched. Returns their names."""
    listed = listed_files(tools)
    for name, digest in listed.items():
        path = tools / name
        try:
            got = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise CheckError(f"{path} cannot be read: {exc}") from exc
        if got != digest:
            raise CheckError(f"{path} is not the file that was fetched: SHA-256 {got}")
        print(f"ok: {name} is the file that was fetched")
    return list(listed)


def run(command: list[str], **options) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, timeout=120, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckError(f"{command[0]} did not run: {exc}") from exc


Runner = Callable[..., subprocess.CompletedProcess]  # ``run``, or a stand-in in a test


def pictures(stream: bytes) -> tuple[int, bool, list[int]]:
    """How many pictures an Annex B stream holds (its slices that begin one: first_mb_in_slice
    0), whether one of them is cut into several slices, and the kinds of its NAL units."""
    count, several, kinds, pos = 0, False, [], 0
    while (i := stream.find(b"\x00\x00\x01", pos)) >= 0 and i + 3 < len(stream):
        kind = stream[i + 3] & 0x1F
        kinds.append(kind)
        if kind in (NAL_SLICE, NAL_IDR):
            if i + 4 < len(stream) and stream[i + 4] & 0x80:
                count += 1
            else:
                several = True
        pos = i + 3
    return count, several, kinds


def program_of(folder: Path) -> Path:
    """The program of Libre Panel in ``folder`` that answers on the command line."""
    found = next((folder / name for name in PROGRAMS if (folder / name).is_file()), None)
    if found is None:
        raise CheckError(f"{folder} has no program of Libre Panel: {', '.join(PROGRAMS)}")
    return found


def asked(program: Path, *args: str, run: Runner = run) -> dict:
    """What the program says (JSON) to ``args``, run with nothing of this computer's Libre
    Panel: its own data, a folder of settings of its own, no engine named elsewhere."""
    hidden = ("LIBRE_PANEL_ENGINE", "LIBRE_PANEL_DATA", "LIBRE_PANEL_HOME")
    env = {key: value for key, value in os.environ.items() if key not in hidden}
    with tempfile.TemporaryDirectory() as place:
        env["LIBRE_PANEL_HOME"] = place
        done = run([str(program), *args], text=True, encoding="utf-8", errors="replace", env=env)
    if done.returncode != 0:
        said = (done.stderr or done.stdout or "").strip()
        raise CheckError(f"{program.name} {' '.join(args)}: exit code {done.returncode} {said}")
    try:
        return json.loads(done.stdout)
    except ValueError as exc:
        raise CheckError(f"{program.name} {' '.join(args)} said {done.stdout[:200]!r}") from exc


def check_discovery(program: Path, tools: Path, names: list[str], run: Runner = run) -> None:
    """The program of this build looks for its programs in this folder first."""
    found = asked(program, "tools", run=run)
    for name in names:
        if name not in ASKED:
            continue
        where = found.get(ASKED[name])
        if where is None:
            raise CheckError(f"started from {program.name}, the app finds no {name}")
        if Path(where).resolve() != (tools / name).resolve():
            raise CheckError(f"started from {program.name}, the app finds {where} and not {name}")
        print(f"ok: started from {program.name}, the app finds {name} in {TOOLS_FOLDER}")


def check_ffmpeg(ffmpeg: Path, program: Path, run: Runner = run) -> None:
    """ffmpeg knows libx264, makes the standby clip as the program asks it and reads it back."""
    banner = run([str(ffmpeg), "-hide_banner", "-version"], text=True).stdout.splitlines()
    encoders = run([str(ffmpeg), "-hide_banner", "-encoders"], text=True)
    if not re.search(r"^\s*V\S*\s+libx264\s", encoders.stdout, re.MULTILINE):
        raise CheckError(f"{ffmpeg} does not list libx264 among its encoders")
    print(f"ok: {banner[0] if banner else ffmpeg} lists libx264")

    with tempfile.TemporaryDirectory() as place:
        out = Path(place) / "standby.h264"
        told = asked(program, "tools", "clip", str(out), run=run)
        if Path(told.get("ffmpeg", "")).resolve() != ffmpeg.resolve():
            raise CheckError(f"the standby clip was made with {told.get('ffmpeg')}, not {ffmpeg}")
        try:
            clip = out.read_bytes()
        except OSError as exc:
            raise CheckError(f"{program.name} tools clip wrote no clip: {exc}") from exc
        made, several, kinds = pictures(clip)
        wanted = told.get("pictures")
        if made != wanted or several or not {NAL_IDR, NAL_SPS, NAL_PPS} <= set(kinds):
            raise CheckError(
                f"{ffmpeg} made a standby clip of {made} pictures of {wanted} (NAL units "
                f"{sorted(set(kinds))}, several slices: {several})"
            )
        read = run([str(ffmpeg), "-v", "error", "-f", "h264", "-i", str(out), "-f", "framecrc",
                    "-"], text=True)  # fmt: skip
    frames = [line for line in read.stdout.splitlines() if line and not line.startswith("#")]
    if read.returncode != 0 or len(frames) != made:
        raise CheckError(
            f"{ffmpeg} read {len(frames)} pictures of the {made} of the standby clip: exit code "
            f"{read.returncode} {read.stderr.strip()[:200]}"
        )
    print(f"ok: {ffmpeg.name} makes the standby clip ({made} pictures, {len(clip)} bytes)")
    print(f"ok: {ffmpeg.name} reads it back")


def engine_record(frame: bytes, number: int) -> bytes:
    """A whole frame as the engine takes it (engine/src/wire.rs, kind 1): one box, all of it."""
    head = b"LPE1" + bytes((1, 0, 0, 0)) + struct.pack("<II", number, 1)
    return head + struct.pack("<4H", 0, 0, *SIZE) + frame


def check_engine(engine: Path, run: Runner = run) -> None:
    """The engine says its version and encodes frames as the video mode feeds it."""
    said = run([str(engine), "version"], text=True)
    if said.returncode != 0 or "x264 build" not in said.stdout:
        raise CheckError(f"{engine} said no version: exit code {said.returncode} {said.stderr}")
    print(f"ok: {said.stdout.strip()}")
    command = [str(engine), "encode", "--size", f"{SIZE[0]}x{SIZE[1]}", *ENGINE_OPTIONS]
    detail = bytes(range(256)) * (SIZE[0] * SIZE[1] * 3 // 256)  # (a picture of some detail)
    fed = b"".join(  # (that moves)
        engine_record(detail[n * 9 :] + detail[: n * 9], n) for n in range(FRAMES)
    )
    done = run(command, input=fed)
    if done.returncode != 0 or not done.stdout:
        error = done.stderr.decode("utf-8", "replace").strip()
        raise CheckError(f"{engine} did not encode: exit code {done.returncode} {error}")
    made, several, kinds = pictures(done.stdout)
    if made != FRAMES or several or not {NAL_IDR, NAL_SPS, NAL_PPS} <= set(kinds):
        raise CheckError(
            f"{engine} encoded {made} pictures of {FRAMES} frames (NAL units "
            f"{sorted(set(kinds))}, several slices: {several})"
        )
    print(f"ok: {engine.name} encodes {FRAMES} frames to {len(done.stdout)} bytes of H.264")


def check_presentmon(presentmon: Path, run: Runner = run) -> None:
    """PresentMon starts and says its version (its help ends with exit code 1, by design)."""
    done = run([str(presentmon), "--help"], text=True, errors="replace")
    shown = f"{done.stdout}\n{done.stderr}"
    version = re.search(r"PresentMon (\d+\.\d+\.\d+)", shown)
    if version is None:
        raise CheckError(f"{presentmon} said no version: exit code {done.returncode} {shown[:200]}")
    print(f"ok: PresentMon {version.group(1)} starts")


def check(folder: Path, run: Runner = run) -> None:
    tools = folder / TOOLS_FOLDER
    if not tools.is_dir():
        raise CheckError(f"{folder} has no {TOOLS_FOLDER} folder")
    names = check_files(tools)
    if not {"ffmpeg", "ffmpeg.exe"} & set(names):
        raise CheckError(f"{tools} has no ffmpeg: the videos and the standby clip need it")
    program = program_of(folder)
    check_discovery(program, tools, names, run)
    for name in names:
        if name in ("ffmpeg", "ffmpeg.exe"):
            check_ffmpeg(tools / name, program, run)
        elif name in ("libre-panel-engine", "libre-panel-engine.exe"):
            check_engine(tools / name, run)
        elif name == "PresentMon.exe":
            check_presentmon(tools / name, run)


def main(argv: list[str] | None = None, run: Runner = run) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("program", type=Path, help="the folder with libre-panel in it")
    args = parser.parse_args(argv)
    try:
        check(args.program, run)
    except CheckError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
