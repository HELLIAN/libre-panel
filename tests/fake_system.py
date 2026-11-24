"""A Steam library by hand, for the tests of the services (test_rust_services.py)."""

from pathlib import Path

MANIFEST = """"AppState"
{
\t"appid"\t\t"%(appid)s"
\t"Universe"\t\t"1"
\t"name"\t\t"%(name)s"
\t"StateFlags"\t\t"4"
\t"installdir"\t\t"%(installdir)s"
}
"""


def make_library(path, *games):
    """A Steam library with the games installed: (appid, name, installdir) each."""
    steamapps = Path(path) / "steamapps"
    (steamapps / "common").mkdir(parents=True, exist_ok=True)
    for appid, name, installdir in games:
        manifest = MANIFEST % {"appid": appid, "name": name, "installdir": installdir}
        (steamapps / f"appmanifest_{appid}.acf").write_text(manifest, encoding="utf-8")
        (steamapps / "common" / installdir).mkdir(parents=True, exist_ok=True)
    return Path(path)
