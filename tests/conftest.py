import faulthandler
import itertools
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from libre_panel import i18n


def pytest_runtest_logreport(report):
    """On GitHub Actions a failed test becomes an annotation of the run, which the
    API shows without the log (whose download may not be reachable)."""
    if not report.failed or os.environ.get("GITHUB_ACTIONS") != "true":
        return
    path, line, _name = report.location
    text = (report.longreprtext or "").strip()[-1500:]
    for char, code in (("%", "%25"), ("\r", "%0D"), ("\n", "%0A")):
        text = text.replace(char, code)
    title = f"{report.nodeid} ({report.when})".replace(",", ";").replace("::", " ")
    # on a line of its own: pytest's progress dots have no line break
    out = f"\n::error file={path},line={(line or 0) + 1},title={title}::{text}\n"
    # what the console cannot write (a "ś" on Windows' cp1252) as "?", not a crash of the run
    encoding = sys.__stdout__.encoding or "utf-8"
    sys.__stdout__.write(out.encode(encoding, "replace").decode(encoding))
    sys.__stdout__.flush()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Whether the test itself passed, for what it kept (golden.py)."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item._golden_passed = report.passed


@pytest.fixture(autouse=True)
def _golden(request, tmp_path_factory):
    """lp-check's answers to the test, against those kept (golden.py), or kept anew."""
    import golden

    if not golden._places:
        root = Path(__file__).resolve().parent.parent
        golden.places(tmp=tmp_path_factory.getbasetemp(), data=root / "data", repo=root)
    mark = request.node.get_closest_marker
    kept = (
        mark("kept") is not None
        and not golden.live_here(mark("live"))
        and golden.kept_here(mark("c_library"))
    )
    golden.begin(Path(str(request.node.fspath)).stem, request.node.name, kept)
    yield
    unasked, untold = golden.unasked(), golden.untold()
    passed = getattr(request.node, "_golden_passed", False)
    golden.end(passed)
    assert not passed or unasked == 0, f"{unasked} of the calls kept for this test were not made"
    assert not passed or not untold, f"not asked for, of what the Python app told: {untold}"


# On GitHub Actions a test that hangs ends the run after ten minutes, with every thread's stack
# in a file that the workflow prints (pytest captures the standard error a test writes to),
# instead of holding the job until its limit cuts it off as "cancelled" with nothing said.
HANG_S = 600
HANG_FILE = Path(os.environ.get("RUNNER_TEMP") or ".") / "hang-traceback.txt"
_hang_out = []


@pytest.fixture(autouse=True)
def _hang_guard():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        yield
        return
    if not _hang_out:
        _hang_out.append(open(HANG_FILE, "w", encoding="utf-8"))  # noqa: SIM115 (the run's)
    faulthandler.dump_traceback_later(HANG_S, exit=True, file=_hang_out[0])
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own config/user-themes directory."""
    home = tmp_path / "home"
    monkeypatch.setenv("LIBRE_PANEL_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def not_elevated(monkeypatch):
    """The tests run with the rights of an ordinary user, whoever runs them (a CI runner on
    Windows is an administrator, a container is root): the settings that name a program change
    unless a test says Libre Panel runs with the highest rights, and plugin folders and programs
    are used wherever they lie (rights.py)."""
    from libre_panel import rights

    monkeypatch.setattr(rights, "elevated", lambda: False)


@pytest.fixture(autouse=True)
def no_installed_plugins(monkeypatch):
    """Plugins installed next to Libre Panel (a mod) stay out of the tests;
    tests install their own as folders."""
    from libre_panel.plugins import loader

    monkeypatch.setattr(loader, "_installed", lambda group: iter(()))
    loader.reset_registry()
    yield
    loader.reset_registry()


@pytest.fixture(autouse=True)
def english(monkeypatch):
    """Tests read English texts, whatever language the machine running them has."""
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture
def bus(monkeypatch):
    """A session bus of the test's own (wayland_bus.py has stand-ins of services on it)."""
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address"],
                              stdout=subprocess.PIPE, text=True)  # fmt: skip
    address = daemon.stdout.readline().strip()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    yield address
    daemon.kill()
    daemon.wait()


_plugin_names = itertools.count()


@pytest.fixture
def plugin_folder(isolated_home):
    """A plugin installed as a folder, as in the Windows setup."""
    created = []

    def make(source, parts, api=1, files=None):
        """``parts``: {group: {name: "attribute"}}; attributes are in the new package
        (a target with ``{package}`` in it is taken as it is, e.g. ``"{package}.themes"``).
        ``files``: more files in the package folder, {relative path: text}."""
        package = f"lp_test_plugin_{next(_plugin_names)}"
        folder = isolated_home / "plugins" / package
        (folder / package).mkdir(parents=True)
        (folder / package / "__init__.py").write_text(textwrap.dedent(source), encoding="utf-8")
        for name, text in (files or {}).items():
            (folder / package / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / package / name).write_text(text, encoding="utf-8")
        tables = "".join(
            f'\n[entry-points."{group}"]\n'
            + "".join(f'"{name}" = "{_target(package, attr)}"\n' for name, attr in names.items())
            for group, names in parts.items()
        )
        (folder / "plugin.toml").write_text(
            f'name = "{package}"\napi = {api}\n{tables}', encoding="utf-8"
        )
        created.append((package, folder))
        _reset_registry()
        return package

    yield make
    for package, folder in created:
        sys.modules.pop(package, None)
        if str(folder) in sys.path:
            sys.path.remove(str(folder))
    _reset_registry()


def _target(package, attr):
    return attr.format(package=package) if "{package}" in attr else f"{package}:{attr}"


def _reset_registry():
    from libre_panel.plugins.loader import reset_registry

    reset_registry()
