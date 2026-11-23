"""The pictures, zips and themes the tests share, and the project's GitHub as they play it:
its list of the community's themes, a zip and a preview (the ``github`` fixture). The app's list
address points at it: the Rust app's by its environment, the Python app's where it is there."""

import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

INDEX_FORMAT = "libre-panel-community/1"  # (the list's, as the app reads it)


def png(colour="#22d3ee", size=(48, 32)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def theme_json(**more):
    data = {
        "format": "libre-panel-theme/1",
        "name": "Shared",
        "display": {"model": "custom", "width": 480, "height": 320},
        "background": {"color": "#000000", "image": "assets/back.png"},
        "widgets": [],
    }
    return json.dumps({**data, **more})


class GitHub(BaseHTTPRequestHandler):
    """The project's GitHub as the tests play it: ``files`` by their path, each request
    counted in ``asked``."""

    files: dict = {}
    asked: list = []

    def do_GET(self):  # noqa: N802 - the server's name
        GitHub.asked.append(self.path)
        found = GitHub.files.get(self.path)
        if isinstance(found, tuple):  # (status, location): a redirect
            self.send_response(found[0])
            self.send_header("Location", found[1])
            self.end_headers()
            return
        self.send_response(200 if found is not None else 404)
        self.end_headers()
        self.wfile.write(found or b"")

    def log_message(self, *args):
        pass


def an_entry(name="Aurora", zip_path="aurora/aurora.zip", sha256="", **more):
    return {"name": name, "author": "Someone", "description": "Northern lights.",
            "sizes": ["480x320", "1920x480"], "preview": "aurora/preview.png", "zip": zip_path,
            "sha256": sha256, **more}  # fmt: skip


@pytest.fixture
def github(monkeypatch):
    """A list of one sound theme (and two that are not) beside a zip and a preview; the
    app's list address points at it. Yields the files, which a test may change."""
    made = ThreadingHTTPServer(("127.0.0.1", 0), GitHub)
    threading.Thread(target=made.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{made.server_address[1]}"
    monkeypatch.setenv("LP_COMMUNITY_INDEX", f"{base}/main/community/themes/index.json")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    # (its folder in the zip has another name: the list's is the one it is installed under)
    theme = zipped({"north/theme.json": theme_json(name="Aurora"), "north/assets/back.png": png()})
    index = {
        "format": INDEX_FORMAT,
        "themes": [
            an_entry(sha256=hashlib.sha256(theme).hexdigest()),
            an_entry(name="Elsewhere", zip_path="../../../other/repo/x.zip", sha256="0" * 64),
            {"name": "Broken"},
        ],
    }
    GitHub.files = {
        "/main/community/themes/index.json": json.dumps(index).encode(),
        "/main/community/themes/aurora/aurora.zip": theme,
        "/main/community/themes/aurora/preview.png": png("#7c3aed"),
    }
    GitHub.asked = []
    yield GitHub.files
    made.shutdown()
    made.server_close()
