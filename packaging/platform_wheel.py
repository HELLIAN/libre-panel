"""A wheel of Libre Panel for one platform: the Python package's wheel (setup.py: the plugin
host's package and Libre Panel's files in share/libre-panel) with the Rust programs and the
libraries they draw with, so that pip and pipx install the Rust app.

    python packaging/platform_wheel.py <the package's wheel> --programs DIR [--libraries DIR]
                                       [--licenses DIR] [--platform TAG] [-o DIR]

On Linux the programs (libre-panel, its window and tray, the engine in ``tools`` with its
licences) go where an install keeps its commands (the wheel's ``.data/scripts``, folders
kept), and the libraries of C that draw as Pillow draws (FreeType, HarfBuzz, libjpeg, libwebp)
into ``lib/libre-panel`` of the install, where the app looks for them beside its ``bin``
(rust/app's name_own_libraries); the Python command ``libre-panel`` goes, the Rust program is
it (pipx links it, and the app finds its files from where the link leads).

On Windows the programs with their DLLs, the engine in ``tools`` and the sensor service in
``sensors``, as the downloads lay them out, go into ``libre-panel`` of the install, beside its
``share/libre-panel``; the command ``libre-panel`` is libre_panel/launch.py, which runs it:
pipx copies the commands it cannot link, and a copied program would have none of its files.

The licences the downloads carry (of the Rust crates in the programs, of the libraries) go
where a wheel keeps its licences (``.dist-info/licenses``, each named in its METADATA); the
engine's stay with it in ``tools``. The platform's tag is the one given, or on Linux the
manylinux tag of the newest glibc the programs ask for.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

PYTHON_COMMAND = "libre-panel = libre_panel.cli:main"
LAUNCHER = "libre-panel = libre_panel.launch:main"
# what a program starts with, which is installed as one (by its content: wherever the wheel is
# made, a licence is no program)
PROGRAM_STARTS = (b"\x7fELF", b"#!")


def _digest(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def glibc_tag(programs: list[Path]) -> str:
    """manylinux_2_<n>_x86_64 for the newest GLIBC_2.<n> a program asks for."""
    newest = 17
    for program in programs:
        if program.suffix in (".dll", ".exe"):
            continue
        done = subprocess.run(["objdump", "-T", str(program)], capture_output=True, text=True)
        for minor in re.findall(r"GLIBC_2\.(\d+)", done.stdout):
            newest = max(newest, int(minor))
    return f"manylinux_2_{newest}_x86_64"


def build(wheel: Path, programs: Path, libraries: list[Path], platform: str, out: Path,
          licenses: Path | None = None) -> Path:  # fmt: skip
    """The platform's wheel made of ``wheel`` with the folder ``programs``, written into
    ``out``: a Windows one (``platform`` win_...) with the app in its own folder and the
    launcher as its command, any other with the programs as the commands."""
    windows = platform.startswith("win")
    with zipfile.ZipFile(wheel) as source:
        entries = {info.filename: source.read(info) for info in source.infolist()}
    info_dir = next(name.split("/")[0] for name in entries if ".dist-info/" in name)
    data_dir = info_dir.replace(".dist-info", ".data")
    name, version = info_dir.removesuffix(".dist-info").rsplit("-", 1)
    entries.pop(f"{info_dir}/RECORD")

    points = f"{info_dir}/entry_points.txt"
    text = entries[points].decode()
    if PYTHON_COMMAND not in text:
        raise SystemExit(f"{wheel}: no {PYTHON_COMMAND!r} to replace")
    lines = [line for line in text.splitlines() if line.strip() != PYTHON_COMMAND]
    if windows:
        lines.insert(lines.index("[console_scripts]") + 1, LAUNCHER)
    entries[points] = ("\n".join(lines) + "\n").encode()
    meta = f"{info_dir}/WHEEL"
    lines = [line for line in entries[meta].decode().splitlines()
             if not line.startswith(("Root-Is-Purelib:", "Tag:"))]  # fmt: skip
    lines += ["Root-Is-Purelib: false", f"Tag: py3-none-{platform}"]
    entries[meta] = ("\n".join(lines) + "\n").encode()

    executable: set[str] = set()
    for program in sorted(file for file in programs.rglob("*") if file.is_file()):
        place = "data/libre-panel" if windows else "scripts"
        path = f"{data_dir}/{place}/{program.relative_to(programs).as_posix()}"
        entries[path] = program.read_bytes()
        if program.suffix == ".exe" or entries[path].startswith(PROGRAM_STARTS):
            executable.add(path)
    for library in libraries:
        entries[f"{data_dir}/data/lib/libre-panel/{library.name}"] = library.read_bytes()
    named = []
    for licence in sorted(licenses.rglob("*") if licenses else []):
        if licence.is_file():
            file = licence.relative_to(licenses).as_posix()
            if f"{info_dir}/licenses/{file}" in entries:
                raise SystemExit(f"{licence}: the package's wheel has a licence of that name")
            entries[f"{info_dir}/licenses/{file}"] = licence.read_bytes()
            named.append(f"License-File: {file}")
    if named:
        metadata = f"{info_dir}/METADATA"
        head, blank, body = entries[metadata].decode().partition("\n\n")
        lines = head.rstrip("\n") + "\n" + "\n".join(named) + "\n"
        entries[metadata] = (lines + ("\n" + body if blank else "")).encode()

    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, data in sorted(entries.items()):
        writer.writerow([path, _digest(data), len(data)])
    writer.writerow([f"{info_dir}/RECORD", "", ""])
    entries[f"{info_dir}/RECORD"] = record.getvalue().encode()

    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}-{version}-py3-none-{platform}.whl"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as made:
        # (the package and its files first, the .dist-info last, as the wheel format asks)
        for path in sorted(entries, key=lambda p: (p.startswith(info_dir), p)):
            item = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path in executable else 0o644
            item.external_attr = (stat.S_IFREG | mode) << 16
            made.writestr(item, entries[path])
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wheel", type=Path, help="the Python package's wheel (py3-none-any)")
    parser.add_argument("--programs", type=Path, required=True,
                        help="the Rust programs' folder (on Windows with their DLLs)")  # fmt: skip
    parser.add_argument("--libraries", type=Path,
                        help="the folder of the libraries they draw with (Linux)")  # fmt: skip
    parser.add_argument("--licenses", type=Path, help="the folder of the downloads' licences")
    parser.add_argument("--platform", help="the wheel's platform tag (else from the programs)")
    parser.add_argument("-o", "--out", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    libraries = sorted(args.libraries.iterdir()) if args.libraries else []
    if not any((args.programs / name).is_file() for name in ("libre-panel", "libre-panel.exe")):
        raise SystemExit(f"no libre-panel program in {args.programs}")
    programs = [file for file in args.programs.rglob("*") if file.is_file()]
    platform = args.platform or (glibc_tag(programs) if sys.platform.startswith("linux")
                                 else "win_amd64")  # fmt: skip
    made = build(args.wheel, args.programs, libraries, platform, args.out, args.licenses)
    print(made)
    return 0


if __name__ == "__main__":
    sys.exit(main())
