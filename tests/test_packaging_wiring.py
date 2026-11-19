"""The release build carries ffmpeg, PresentMon and PawnIO's installer, and proves it on each
system. CI is the only place that builds the downloads, so these keep the pieces joined: the
fetch comes before the build, the checks run on the built folder, on what the setup installed
and on the AppImage's files, and every place agrees on what the folder is called."""

import importlib.util
import os
import re
import sys
from pathlib import Path

from libre_panel import bundled

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def job(workflow, name):
    """The text of one job of a workflow."""
    start = workflow.index(f"\n  {name}:\n") + 1
    found = re.search(r"\n  [a-z][a-z0-9-]*:\n", workflow[start + 1 :])
    return workflow[start : start + 1 + found.start()] if found else workflow[start:]


def test_the_release_fetches_before_it_builds_and_checks_the_built_folder():
    """The release's downloads are those CI builds and tries (its jobs `try` and `appimage`):
    release.yml calls ci.yml, and publishes only once all of it passed."""
    release = read(".github", "workflows", "release.yml")
    assert "uses: ./.github/workflows/ci.yml" in job(release, "app")
    assert "needs: [test, app]" in job(release, "github-release")
    ci = read(".github", "workflows", "ci.yml")
    assert "\n  workflow_call:\n" in ci[: ci.index("\njobs:")]
    rust = job(ci, "rust")
    # (Windows: the tools fetched before the folder is made, which carries them)
    fetch = rust.index('python packaging/fetch_tools.py "$RUNNER_TEMP/tools"')
    folder = rust.index("- name: libre-panel to try on a PC (Windows)")
    assert fetch < folder and 'cp -r "$RUNNER_TEMP/tools/." libre-panel-rust/tools/' in rust
    tried = job(ci, "try")
    assert "test -f libre-panel-rust/tools/ffmpeg.exe" in tried
    assert tried.index("ISCC.exe") < tried.index("test_installer.py --rust")
    appimage = job(ci, "appimage")
    fetch = appimage.index('python packaging/fetch_tools.py "$RUNNER_TEMP/tools"')
    build = appimage.index("packaging/linux/build_appimage.sh")
    check = appimage.index("RUST=1 packaging/linux/test_appimage.sh")
    assert fetch < build < check
    # for both systems: neither step is held back by an ``if:``
    before = appimage[appimage.rindex("- name:", 0, fetch) : fetch]
    assert "if:" not in before
    assert "if:" not in appimage[appimage.rindex("- name:", 0, check) : check]


def test_the_setup_and_the_appimage_are_proven_with_the_same_check():
    installer = read("packaging", "windows", "test_installer.py")
    assert "check_tools.py" in installer and '"--tools"' in installer
    assert "PawnIO_setup.exe" in installer
    appimage = read("packaging", "linux", "test_appimage.sh")
    assert "--appimage-extract" in appimage
    assert 'packaging/check_tools.py" "$unpacked/squashfs-root/usr/lib/libre-panel"' in appimage
    # (both starts, without and with a tray, with the tools' check)
    assert "smoke=(--tools)" in appimage and appimage.count("smoke_test.py") == 2
    assert '"${smoke[@]}"' in appimage and "${smoke[*]}" in appimage


def test_the_appimage_is_built_on_the_oldest_ubuntu_and_checked_for_the_glibc_it_needs(tmp_path):
    """An AppImage takes glibc from the system it runs on: built on a new runner, it asked for
    GLIBC_2.38 and did not start on the oldest Ubuntu LTS still supported (the AppImage
    catalog's test system)."""
    ci = read(".github", "workflows", "ci.yml")
    assert "runs-on: ubuntu-22.04" in job(ci, "appimage")
    assert re.search(r"- os: ubuntu-22\.04\n\s+name: linux", job(ci, "plugin-host"))
    appimage = read("packaging", "linux", "test_appimage.sh")
    assert 'check_glibc.py" "$unpacked/squashfs-root" "${GLIBC_LIMIT:-2.35}"' in appimage
    check = load("check_glibc_wiring", PACKAGING / "linux" / "check_glibc.py")
    assert check.shown((2, 38, 0)) == "2.38" and check.shown(()) == "none"
    (tmp_path / "notes.txt").write_text("not a program", encoding="utf-8")
    assert check.main(["check_glibc.py", str(tmp_path), "2.35"]) == 1  # nothing to check: said


def test_the_downloads_carry_the_notice_that_names_their_sources():
    ci = read(".github", "workflows", "ci.yml")
    assert "Copy-Item LICENSE, README.md, NOTICE.md libre-panel-rust/" in job(ci, "try")
    assert "cp NOTICE.md libre-panel-rust/licenses/" in job(ci, "rust")  # Windows
    assert "cp LICENSE README.md NOTICE.md dist/libre-panel/" in job(ci, "appimage")  # Linux
    assert 'cp NOTICE.md "$app/licenses/"' in job(ci, "appimage")


def test_the_spec_puts_the_fetched_folder_next_to_the_program_under_the_name_the_app_looks_for():
    spec = read("packaging", "pyinstaller", "libre-panel.spec")
    fetch_tools = load("fetch_tools_wiring", PACKAGING / "fetch_tools.py")
    assert fetch_tools.DEFAULT_FOLDER.name == "bundled-tools"
    assert 'ROOT / "packaging" / "bundled-tools"' in spec
    assert "from libre_panel.bundled import TOOLS_FOLDER" in spec
    assert 'program_folder = Path(DISTPATH) / "libre-panel"' in spec  # the built folder
    assert "copytree(tools, program_folder / TOOLS_FOLDER" in spec  # and next to the program
    check_tools = load("check_tools_wiring", PACKAGING / "check_tools.py")
    assert check_tools.TOOLS_FOLDER == bundled.TOOLS_FOLDER == "tools"
    # the setup clears the same folder on an update
    assert re.search(
        r'^Type: filesandordirs; Name: "\{app\}\\tools"$',
        read("packaging", "windows", "libre-panel.iss"),
        re.M,
    )


def test_the_names_the_app_looks_for_are_the_ones_the_table_installs(monkeypatch):
    fetch_tools = load("fetch_tools_wiring", PACKAGING / "fetch_tools.py")
    for platform, system in (("windows", "win32"), ("linux", "linux")):
        monkeypatch.setattr(sys, "platform", system)
        files = {tool.file for tool in fetch_tools.TOOLS if tool.platform == platform}
        assert bundled.program_name("ffmpeg") in files
        if platform == "windows":
            assert {bundled.program_name("PresentMon"), bundled.PAWNIO_SETUP} <= files
            assert len(files) == 3  # and nothing else that the app does not know of
        else:
            assert files == {"ffmpeg"}


def test_the_setup_script_is_read_as_utf8():
    """Inno Setup reads a script as UTF-8 when it starts with the byte order mark; without it
    the German texts of the setup ("Für alle Sensoren …") would come out garbled."""
    script = ROOT.joinpath("packaging", "windows", "libre-panel.iss").read_bytes()
    assert script.startswith(b"\xef\xbb\xbf")
    assert "de.PrivilegesRequiredOverrideCurrentUser=Nur für &mich installieren" in script.decode()


def test_the_setup_starts_the_app_as_the_user_who_ran_it():
    """The setup runs as administrator. An app it started as such made its private folder for
    the administrators alone (Python's owner-only folders on Windows), and every later start as
    the user hung there (discussion #22): what it runs after the install runs as the user."""
    script = read("packaging", "windows", "libre-panel.iss")
    run = script.split("\n[Run]\n", 1)[1].split("\n[", 1)[0]
    lines = [line for line in run.splitlines() if line.startswith("Filename:")]
    assert len(lines) == 3 and all("runasoriginaluser" in line for line in lines)


def test_the_language_chosen_in_the_setup_is_the_apps_on_a_new_install():
    """The setup's language becomes the app's (config.toml's `language`, as Settings → Language
    sets it) on a new install where it was chosen, not taken as the setup offered it (the one
    Windows speaks, else English: the app speaks Windows' anyway); the app keeps a language
    chosen in it (`config language --if-auto`), and an update leaves it as it is. The setup's
    languages are the app's codes, and whether Libre Panel was installed before is asked
    before the setup registers itself, under the AppId's key."""
    script = read("packaging", "windows", "libre-panel.iss")
    run = script.split("\n[Run]\n", 1)[1].split("\n[", 1)[0]
    (line,) = [line for line in run.splitlines() if "config language" in line]
    assert 'Parameters: "config language {language} --if-auto"' in line
    assert "Check: LanguageChosen" in line and "runasoriginaluser" in line
    languages = re.findall(r'^Name: "(\w+)"; MessagesFile:', script, re.M)
    assert languages == ["en", "de"]  # (the first is the one offered where Windows' is neither)
    app_id = re.search(r"^AppId=\{(\{[0-9A-F-]+\})$", script, re.M)
    assert app_id
    first = script.split("<event('InitializeSetup')>", 1)[1].split("\nend;", 1)[0]
    assert f"\\Uninstall\\{app_id[1]}_is1'" in first
    assert all(f"RegKeyExists({root}, Key)" in first for root in ("HKLM64", "HKLM32", "HKCU"))
    chosen = script.split("function LanguageChosen: Boolean;", 1)[1].split("\nend;", 1)[0]
    assert "(GetUILanguage and $3FF) = $07" in chosen and "Offered := 'de'" in chosen
    assert "Offered := 'en'" in chosen
    assert "Result := NewInstall and (ActiveLanguage <> Offered);" in chosen


def test_ci_runs_every_test_that_needs_a_browser_where_there_is_one():
    """Only CI's desktop job has Playwright and Chromium: elsewhere a test that needs them skips.
    A file of such tests left out of its list would never run anywhere."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci.split("\n  desktop:", 1)[1]
    listed = set(re.findall(r"tests/[\w*]+\.py", job))
    tests = ROOT / "tests"
    uses = re.compile(r"^(?:from|import) playwright|importorskip\(\s*\"playwright", re.M)
    needing = {
        path.name
        for path in tests.glob("test_*.py")
        if uses.search(path.read_text(encoding="utf-8")) and not path.name.startswith("test_zz")
    }
    assert "test_app_browser.py" in needing
    for name in sorted(needing):
        assert any(Path(name).match(Path(pattern).name) for pattern in listed), name


def test_a_release_candidate_is_never_for_everyone():
    """A pre-release is a draft that only those who may write to the repository see, by hand as
    by tag; a run keeps none of its files (on a public repository anyone signed in to GitHub
    could download them), after the release, whether it ran or not."""
    workflow = read(".github", "workflows", "release.yml")
    publish = workflow[workflow.index("- name: Publish the release with all files") :]
    assert 'if [ "$pre" = true ]; then flags+=(--draft); fi' in publish  # a pushed tag
    assert 'artifacts/* --repo "$GITHUB_REPOSITORY" --draft' in publish  # Run workflow
    assert 'echo "::error::$TAG is published already"' in publish  # never one made public
    forget = workflow[workflow.index("  forget-the-files:") :]
    assert "if: always()" in forget and "needs: [test, app, github-release]" in forget
    assert 'gh api -X DELETE "repos/$GITHUB_REPOSITORY/actions/artifacts/$id"' in forget


def test_presentmon_is_a_box_of_the_setup_that_says_first_what_will_not_work_without_it():
    """PresentMon (game frame rates) has a box of its own in the Windows setup, checked. Left
    unchecked, the setup says before anything is installed what will not work (the frame
    rates, but where RTSS gives them, and in any case who holds them down), and "No" goes back
    to the boxes; the file is left out, and the membership in Performance Log Users, which only
    PresentMon needs, is not given. A silent setup takes its boxes as given."""
    script = read("packaging", "windows", "libre-panel.iss")
    fetch_tools = load("fetch_tools_wiring", PACKAGING / "fetch_tools.py")
    assert "PresentMon.exe" in {tool.file for tool in fetch_tools.TOOLS}  # (the file left out)
    assert re.search(r'^Name: "presentmon"; Description: "\{cm:PresentMon\}"$', script, re.M)
    files = script.split("\n[Files]\n", 1)[1].split("\n[", 1)[0]
    (everything,) = [line for line in files.splitlines() if 'Source: "{#SourceDir}\\*"' in line]
    assert 'Excludes: "\\tools\\PresentMon.exe"' in everything
    assert (
        'Source: "{#SourceDir}\\tools\\PresentMon.exe"; DestDir: "{app}\\tools"; '
        "Flags: ignoreversion; Tasks: presentmon"
    ) in files
    code = script.split("<event('NextButtonClick')>", 1)[1].split("\nend;", 1)[0]
    assert "CurPageID = wpSelectTasks" in code and "not WizardSilent" in code
    assert "WizardIsTaskSelected('presentmon')" in code and "CustomMessage('PresentMonOff')" in code
    assert "= IDYES" in code  # ("No" stays on the page with the boxes)
    for language in ("en", "de"):
        said = re.search(rf"^{language}\.PresentMonOff=(.+)$", script, re.M).group(1)
        assert "RTSS" in said and "PresentMon" in said
        assert "%" not in said.replace("%n", "")  # (Inno Setup's placeholders begin with one)
    assert re.search(
        r'Parameters: "system-setup --perf-log-users".*Tasks: perflogusers and presentmon$',
        script,
        re.M,
    )
    tester = read("packaging", "windows", "test_installer.py")
    assert 'TASKS = "autostart,presentmon,' in tester
    assert 'install(setup, TASKS.replace("presentmon,", ""), chosen)' in tester


def test_the_windows_programs_say_who_made_them_and_start_with_a_loader_of_their_own():
    """Defender's machine learning took 0.7.10's setup for a trojan (Trojan:Win32/Bearfoos.A!ml).
    Every program of the Windows download says who made it (a version resource: the Rust
    programs', rust/winres.rs, checked in CI's job `rust`; the plugin host's and the sensor
    service's), the plugin host starts with a PyInstaller loader compiled on the runner, not the
    ready-made one that malware shares (own_loader.py fails the build where it is that one), and
    every download gets GitHub's signed attestation of where it was built, before the release
    is published."""
    ci = read(".github", "workflows", "ci.yml")
    rust = job(ci, "rust")
    folder = rust.index("- name: libre-panel to try on a PC (Windows)")
    resource = rust.index('$info.CompanyName -ne "Libre Panel contributors"')
    assert folder < resource
    for name in (
        "libre-panel.exe",
        "LibrePanel.exe",
        "libre-panel-window.exe",
        "libre-panel-tray.exe",
    ):
        assert f'"{name}"' in rust[resource - 400 : resource]
    host = job(ci, "plugin-host")
    install = host.index(
        "PYINSTALLER_COMPILE_BOOTLOADER=1 pip install --no-binary pyinstaller pyinstaller"
    )
    check = host.index("python packaging/windows/own_loader.py shipped-pyinstaller/*.whl")
    build = host.index("pyinstaller --noconfirm --clean packaging/pyinstaller/libre-panel-plugins")
    assert install < check < build
    spec = read("packaging", "pyinstaller", "libre-panel-plugins.spec")
    assert "version=version_resource()," in spec
    assert '("CompanyName", "Libre Panel contributors")' in spec
    assert "<Company>Libre Panel contributors</Company>" in read(
        "sensorhost", "LibrePanel.Sensors.csproj"
    )
    workflow = read(".github", "workflows", "release.yml")
    release = workflow[workflow.index("  github-release:") : workflow.index("  forget-the-files:")]
    assert "id-token: write" in release and "attestations: write" in release
    attest = release.index("uses: actions/attest@v4")
    assert release.index("- name: SHA-256 sums") < attest < release.index("- name: Publish the")
    for kind in ("exe", "zip", "AppImage", "tar.gz", "whl"):
        assert f"artifacts/*.{kind}\n" in release


def test_own_loader_fails_where_the_loader_is_the_one_pyinstaller_ships(tmp_path, monkeypatch):
    import types
    import zipfile

    package = tmp_path / "PyInstaller"
    loaders = package / "bootloader" / "Windows-64bit-intel"
    loaders.mkdir(parents=True)
    fake = types.ModuleType("PyInstaller")
    fake.__file__ = str(package / "__init__.py")
    monkeypatch.setitem(sys.modules, "PyInstaller", fake)
    wheel = tmp_path / "pyinstaller-9.9-py3-none-win_amd64.whl"
    with zipfile.ZipFile(wheel, "w") as shipped:
        for name in ("run.exe", "runw.exe"):
            shipped.writestr(f"PyInstaller/bootloader/Windows-64bit-intel/{name}", b"MZ ready-made")
    own_loader = load("own_loader_wiring", PACKAGING / "windows" / "own_loader.py")
    (loaders / "run.exe").write_bytes(b"MZ compiled here")
    (loaders / "runw.exe").write_bytes(b"MZ compiled here, windowed")
    assert own_loader.main(str(wheel)) == 0
    (loaders / "runw.exe").write_bytes(b"MZ ready-made")  # (the shipped one)
    assert own_loader.main(str(wheel)) == 1
    (loaders / "run.exe").unlink()  # (none at all)
    assert own_loader.main(str(wheel)) == 1


def test_the_release_builds_the_dotnet_sensor_service_and_the_spec_takes_it():
    """The sensor service of the Windows downloads is the .NET program of sensorhost/, built after
    LibreHardwareMonitor's libraries are fetched, with the reference assemblies pinned and the
    release's version (CI's job sensor-host, which the release runs), and carried in "sensors"
    beside the app (its job `try`); the setup clears its libraries before an update."""
    ci = read(".github", "workflows", "ci.yml")
    host = job(ci, "sensor-host")
    fetch = host.index("python packaging/windows/fetch_lhm.py")
    build = host.index("dotnet build sensorhost -c Release -p:RestoreLockedMode=true")
    smoke = host.index("run: sensorhost/bin/Release/libre-panel-sensors.exe --check")
    assert fetch < build < smoke
    assert '-p:InformationalVersion="$version"' in host  # (its version: the release's)
    tried = job(ci, "try")
    assert "name: sensors-windows\n          path: libre-panel-rust/sensors" in tried
    assert "test -d libre-panel-rust/sensors/lhm" in tried
    assert 'Name: "{app}\\sensors\\lhm"' in read("packaging", "windows", "libre-panel.iss")
    project = read("sensorhost", "LibrePanel.Sensors.csproj")
    assert "<AssemblyName>libre-panel-sensors</AssemblyName>" in project
    assert "<Company>Libre Panel contributors</Company>" in project


def test_the_programs_are_built_without_docstrings_and_their_help_does_not_need_one():
    """Python's code in the downloads is compiled as by ``python -OO`` (each process holds less
    of it): the command line's help then still says what Libre Panel is."""
    spec = read("packaging", "pyinstaller", "libre-panel.spec")
    assert "OPTIMIZE = 2" in spec
    assert spec.count("optimize=OPTIMIZE") == 2  # (the programs, and the sensor service)
    from libre_panel import cli

    parser = cli.build_parser()
    assert parser.description == cli.DESCRIPTION and "Libre Panel" in parser.description


def test_an_install_gets_every_file_of_the_data_folder_where_both_apps_look(monkeypatch):
    """Libre Panel's files (the data folder) go whole into an install's share/libre-panel
    (setup.py), where src/libre_panel/datadir.py and rust/app/src/home.rs look for them; the
    downloads copy the folder there too (ci.yml), and the version is pyproject.toml's alone."""
    from types import SimpleNamespace

    from libre_panel import __version__, datadir

    asked = {}
    monkeypatch.setitem(sys.modules, "setuptools", SimpleNamespace(setup=asked.update))
    monkeypatch.chdir(ROOT)
    load("libre_panel_setup", ROOT / "setup.py")
    installed = {Path(target, Path(source).name).as_posix()
                 for target, sources in asked["data_files"] for source in sources}  # fmt: skip
    data = ROOT / "data"
    files = {path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file()}
    assert installed == {f"share/libre-panel/{name}" for name in files}
    assert {"themes/lumen/theme.json", "fonts/Barlow-Medium.ttf", "locale/de.json",
            "editor/app/index.html", "pages/web/index.html"} <= files  # fmt: skip
    assert datadir.DATA == data and datadir.holds_data(data)
    assert datadir.SHARE.as_posix() == "share/libre-panel"
    assert '.join("share").join("libre-panel")' in read("rust", "app", "src", "home.rs")
    assert read(".github", "workflows", "ci.yml").count("cp -r data/. ") == 2
    package = ROOT / "src" / "libre_panel"
    kept = [path for path in package.rglob("*") if path.is_file() and path.suffix != ".py"
            and "__pycache__" not in path.parts]  # fmt: skip
    assert not kept  # (the package is its code alone)
    project = re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M)[1]
    assert __version__ == project
    assert '__version__ = "' not in read("src", "libre_panel", "__init__.py")


def _pure_wheel(folder):
    """A wheel as ``python -m build --wheel`` makes the Python package's: its code, a file of
    Libre Panel's in share/libre-panel, the Python command and the plugin host's."""
    import zipfile

    wheel = folder / "libre_panel-1.2.3-py3-none-any.whl"
    info = "libre_panel-1.2.3.dist-info"
    with zipfile.ZipFile(wheel, "w") as made:
        made.writestr("libre_panel/__init__.py", "")
        made.writestr("libre_panel-1.2.3.data/data/share/libre-panel/themes/a.json", "{}")
        made.writestr(f"{info}/METADATA", "Metadata-Version: 2.4\nName: libre-panel\n"
                      "Version: 1.2.3\n")  # fmt: skip
        made.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: setuptools\n"
                      "Root-Is-Purelib: true\nTag: py3-none-any\n")  # fmt: skip
        points = ("[console_scripts]\nlibre-panel = libre_panel.cli:main\n"
                  "libre-panel-plugins = libre_panel.plugin_host:main\n\n[libre_panel.sensors]\n"
                  "demo = libre_panel.sensors.demo:DemoProvider\n")  # fmt: skip
        made.writestr(f"{info}/entry_points.txt", points)
        made.writestr(f"{info}/RECORD", "")
    return wheel


def _files(folder, names):
    for name, data in names.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if name.split("/")[-1] in ("libre-panel", "libre-panel-window", "libre-panel-engine"):
            path.chmod(0o755)
    return folder


def _checked(wheel):
    """The wheel's files, after checking its RECORD names each with its digest and size and
    the .dist-info comes last."""
    import base64
    import csv
    import hashlib
    import io
    import zipfile

    with zipfile.ZipFile(wheel) as made:
        infos = made.infolist()
        files = {info.filename: made.read(info) for info in infos}
    record = next(name for name in files if name.endswith(".dist-info/RECORD"))
    rows = {row[0]: row[1:] for row in csv.reader(io.StringIO(files[record].decode()))}
    assert set(rows) == set(files) and rows[record] == ["", ""]
    for name, data in files.items():
        if name != record:
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
            assert rows[name] == ["sha256=" + digest.decode(), str(len(data))], name
    names = [info.filename for info in infos]
    assert all(".dist-info/" in name for name in names[-4:]), names
    modes = {info.filename: info.external_attr >> 16 & 0o777 for info in infos}
    return files, modes


def test_the_wheels_pip_and_pipx_install_carry_the_rust_app(tmp_path):
    """packaging/platform_wheel.py: the Python package's wheel with the Rust app, for Linux
    with the programs as the commands and Pillow's libraries beside the environment's bin, for
    Windows with the app in a folder of its own and libre_panel/launch.py as its command; the
    names end as the app's updates look for them among a release's files."""
    platform_wheel = load("platform_wheel", PACKAGING / "platform_wheel.py")
    pure = _pure_wheel(tmp_path)
    linux = _files(tmp_path / "linux", {
        "libre-panel": b"\x7fELF app", "libre-panel-window": b"\x7fELF window",
        "tools/libre-panel-engine": b"\x7fELF engine", "tools/licenses/x264.txt": b"GPL",
        "tools/run.sh": b"#!/bin/sh\n",
    })  # fmt: skip
    lib = _files(tmp_path / "lib", {"libfreetype-1a2b.so.6": b"\x7fELF ft"})
    licenses = _files(tmp_path / "licenses", {"THIRD-PARTY-RUST.txt": b"MIT", "x/a.txt": b"BSD"})
    made = platform_wheel.build(pure, linux, sorted(lib.iterdir()), "manylinux_2_35_x86_64",
                                tmp_path / "out", licenses)  # fmt: skip
    assert made.name == "libre_panel-1.2.3-py3-none-manylinux_2_35_x86_64.whl"
    assert made.name.endswith("_x86_64.whl")  # (updates.py's WHEEL_ENDING off Windows)
    files, modes = _checked(made)
    data = "libre_panel-1.2.3.data"
    assert files[f"{data}/scripts/libre-panel"] == b"\x7fELF app"
    assert files[f"{data}/scripts/tools/libre-panel-engine"] == b"\x7fELF engine"
    assert files[f"{data}/data/lib/libre-panel/libfreetype-1a2b.so.6"] == b"\x7fELF ft"
    assert files[f"{data}/data/share/libre-panel/themes/a.json"] == b"{}"
    # (programs by what they start with, wherever the wheel is made: a licence is none)
    assert modes[f"{data}/scripts/libre-panel"] == modes[f"{data}/scripts/tools/run.sh"] == 0o755
    assert modes[f"{data}/scripts/tools/licenses/x264.txt"] == 0o644
    points = files["libre_panel-1.2.3.dist-info/entry_points.txt"].decode()
    assert "libre_panel.cli:main" not in points and "libre_panel.launch" not in points
    assert "libre-panel-plugins = libre_panel.plugin_host:main" in points
    assert "demo = libre_panel.sensors.demo:DemoProvider" in points
    info = "libre_panel-1.2.3.dist-info"
    assert files[f"{info}/licenses/THIRD-PARTY-RUST.txt"] == b"MIT"
    assert files[f"{info}/licenses/x/a.txt"] == b"BSD"
    metadata = files[f"{info}/METADATA"].decode()
    assert metadata.endswith("1.2.3\nLicense-File: THIRD-PARTY-RUST.txt\nLicense-File: x/a.txt\n")
    wheel_meta = files["libre_panel-1.2.3.dist-info/WHEEL"].decode()
    assert "Root-Is-Purelib: false\n" in wheel_meta
    assert "Tag: py3-none-manylinux_2_35_x86_64\n" in wheel_meta
    assert "py3-none-any" not in wheel_meta

    windows = _files(tmp_path / "windows", {
        "libre-panel.exe": b"MZ app", "LibrePanel.exe": b"MZ windowed", "freetype.dll": b"MZ ft",
        "tools/libre-panel-engine.exe": b"MZ engine",
        "sensors/libre-panel-sensors.exe": b"MZ sensors",
    })  # fmt: skip
    made = platform_wheel.build(pure, windows, [], "win_amd64", tmp_path / "out")
    assert made.name.startswith("libre_panel-1.2.3-")  # (a licence's name is not the wheel's)
    assert made.name == "libre_panel-1.2.3-py3-none-win_amd64.whl"
    assert made.name.endswith("-win_amd64.whl")  # (WHEEL_ENDING on Windows)
    files, modes = _checked(made)
    assert files[f"{data}/data/libre-panel/libre-panel.exe"] == b"MZ app"
    assert files[f"{data}/data/libre-panel/sensors/libre-panel-sensors.exe"] == b"MZ sensors"
    assert files[f"{data}/data/libre-panel/tools/libre-panel-engine.exe"] == b"MZ engine"
    assert not any(name.startswith(f"{data}/scripts/") for name in files)
    assert modes[f"{data}/data/libre-panel/libre-panel.exe"] == 0o755
    assert modes[f"{data}/data/libre-panel/freetype.dll"] == 0o644
    points = files["libre_panel-1.2.3.dist-info/entry_points.txt"].decode()
    assert points.startswith("[console_scripts]\n" + platform_wheel.LAUNCHER + "\n")
    assert "libre_panel.cli:main" not in points
    from libre_panel import launch

    assert (launch.FOLDER, "libre-panel.exe") == ("libre-panel", windows.joinpath(
        "libre-panel.exe").name)  # fmt: skip
    assert platform_wheel.LAUNCHER == "libre-panel = libre_panel.launch:main"


def test_pip_installs_the_linux_wheel_as_the_app_looks_for_its_files(tmp_path):
    """pip puts the programs in the environment's bin (folders kept: the engine in bin/tools),
    the libraries in lib/libre-panel beside it and Libre Panel's files in share/libre-panel,
    where rust/app looks for each: home.rs, name_own_libraries, the engine's tools. (Into a
    folder of its own, and --ignore-installed: an install into a prefix would otherwise
    take a Libre Panel installed where the tests run out of it.)"""
    import subprocess

    pytest = __import__("pytest")
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode:
        pytest.skip("no pip here")
    platform_wheel = load("platform_wheel", PACKAGING / "platform_wheel.py")
    pure = _pure_wheel(tmp_path)
    programs = _files(tmp_path / "programs", {
        "libre-panel": b"\x7fELF app", "tools/libre-panel-engine": b"\x7fELF engine",
    })  # fmt: skip
    lib = _files(tmp_path / "lib", {"libjpeg-9c8d.so.62": b"\x7fELF jpeg"})
    made = platform_wheel.build(pure, programs, sorted(lib.iterdir()),
                                "manylinux_2_35_x86_64", tmp_path / "out")  # fmt: skip
    prefix = tmp_path / "prefix"
    done = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--no-compile",
         "--disable-pip-version-check", "--ignore-requires-python", "--ignore-installed",
         "--prefix", str(prefix), str(made)],  # fmt: skip
        capture_output=True, text=True,
    )  # fmt: skip
    if "is not a supported wheel on this platform" in done.stderr:
        pytest.skip("not a Linux x86-64 with glibc 2.35 or newer")
    assert done.returncode == 0, done.stderr
    bin_ = prefix / "bin"
    assert (bin_ / "libre-panel").read_bytes() == b"\x7fELF app"
    assert os.access(bin_ / "libre-panel", os.X_OK)
    assert (bin_ / "tools" / "libre-panel-engine").read_bytes() == b"\x7fELF engine"
    assert (prefix / "lib" / "libre-panel" / "libjpeg-9c8d.so.62").is_file()
    assert (prefix / "share" / "libre-panel" / "themes" / "a.json").is_file()
    assert (bin_ / "libre-panel-plugins").is_file()
    assert len(list(prefix.glob("lib/python*/site-packages/libre_panel/__init__.py"))) == 1


def test_the_windows_command_runs_the_app_of_its_environment(tmp_path, monkeypatch):
    """libre_panel/launch.py: the app in ``libre-panel`` of the environment, else where the
    package's list of files says (a user's install); run with the arguments given."""
    from types import SimpleNamespace

    from libre_panel import launch

    app = tmp_path / "env" / "libre-panel" / launch.PROGRAM
    app.parent.mkdir(parents=True)
    app.write_bytes(b"app")
    monkeypatch.setattr(launch.sys, "prefix", str(tmp_path / "env"))
    assert launch.program() == app
    # a user's install: not under the environment, found from the package's files
    monkeypatch.setattr(launch.sys, "prefix", str(tmp_path / "elsewhere"))
    listed = SimpleNamespace(name=launch.PROGRAM, parent=SimpleNamespace(name="libre-panel"),
                             locate=lambda: app)  # fmt: skip
    other = SimpleNamespace(name="x.py", parent=SimpleNamespace(name="libre_panel"),
                            locate=lambda: tmp_path)  # fmt: skip
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "distribution",
                        lambda name: SimpleNamespace(files=[other, listed]))  # fmt: skip
    assert launch.program() == app
    monkeypatch.setattr(importlib.metadata, "distribution",
                        lambda name: SimpleNamespace(files=[other]))  # fmt: skip
    with __import__("pytest").raises(SystemExit, match="not here"):
        launch.program()
    monkeypatch.setattr(launch.sys, "prefix", str(tmp_path / "env"))
    monkeypatch.setattr(launch.sys, "argv", ["libre-panel", "render", "--demo"])
    ran = []
    monkeypatch.setattr(launch.os, "execv", lambda path, args: ran.append(("exec", path, args)))
    monkeypatch.setattr(launch.subprocess, "call", lambda args: ran.append(("call", args)) or 3)
    result = launch.main()
    if launch.os.name == "nt":
        assert result == 3 and ran == [("call", [str(app), "render", "--demo"])]
    else:
        assert ran[0] == ("exec", app, [str(app), "render", "--demo"])


def test_ci_builds_and_tries_both_wheels_and_the_release_carries_them():
    """Both wheels are built in the jobs that build the downloads, installed into an
    environment of their own and tried (a frame, the version, the engine, the plugin host);
    the release attaches them, not the Python package's own, and nothing goes to PyPI."""
    ci = read(".github", "workflows", "ci.yml")
    for name, platform in (("appimage", "-o dist"), ("try", "--platform win_amd64 -o dist")):
        steps = job(ci, name)
        made = steps.index("python packaging/platform_wheel.py")
        assert platform in steps[made : made + 200], name
        tries = ("render --demo --theme lumen", '"$app" version', 'grep "engine here"',
                 'python packaging/check_plugin_host.py --app "$app"')  # fmt: skip
        for tried in tries:
            assert made < steps.index(tried, made), (name, tried)
    assert "name: wheel-linux" in job(ci, "appimage") and "name: wheel-windows" in job(ci, "try")
    assert '! -iname "ffmpeg*"' in job(ci, "try") and '! -name "ffmpeg*"' in job(ci, "appimage")
    release = read(".github", "workflows", "release.yml")
    assert "python-package" not in release
    for name in ("libre-panel-rust-setup", "libre-panel-rust-appimage", "wheel-linux",
                 "wheel-windows"):  # fmt: skip
        assert f"name: {name}\n" in job(release, "github-release"), name
    assert "pypi" not in release.lower()
    assert "py3-none-win_amd64.whl" in job(release, "github-release")
