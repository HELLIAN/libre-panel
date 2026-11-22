"""What the Python app's server answered the comparisons of the Rust app's server
(test_rust_server.py), kept (docs/RUST.md, R12 the switch): to each request of a test, in their
order, its status line, its headers and its body, and what it wrote into its settings folder
on the way. A comparison plays them where it asked editor/server.py: the Rust app's server is
asked as before, and what it answers is held against what was kept.

What depends on the run is taken out of what is kept, and put back as the run has it: the
server's settings folder and the test's own (each a word in angle brackets, by the way the
path is spelled), the ports of the servers a test starts on this computer, the length of a
body that holds such. A body or a file larger than WHOLE is kept as its digest. Where what the
Rust app's server answers is the kept answer but for those, the answer played is that very
one with the Python server's folder in place of its own, so that a test goes on reading it; where
it is another, the kept one is played as far as it was kept (a digest stays a digest), and the
test says what differs.

What the Python server wrote into a folder both servers share (``shares``) is kept whole, and
put there before the Rust app's server is asked, as when both were asked in turn.

A test that waits for a server's work asks the same again and again, as often as the work
takes: more often or less often than when the answers were kept. So a request asked once more
than was kept gets the last answer again, and answers kept for a request that is not asked again
are passed over."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

Answer = tuple[str, dict[str, str], bytes]
WHOLE = 4096
# a port a server of this computer was given for the run (32768 and above: one a test names
# itself, as 8765, is told as it is), after the computer's name or as a number called "port"
PORT = re.compile(
    rb"((?:127\.0\.0\.1|localhost)(?::|%3A)|\\?\"port\\?\": )"
    rb"(3276[89]|327[7-9]\d|32[89]\d\d|3[3-9]\d{3}|[45]\d{4}|6[0-4]\d{3}|65[0-4]\d\d|655[0-2]\d|6553[0-5])"
    rb"(?!\d)"
)


class Places:
    """What depends on the run in what a server says and writes: its settings folder ``home``
    and the test's folder ``tmp``."""

    def __init__(self, home: Path, tmp: Path) -> None:
        found = []
        for word, path in (("home", home), ("tmp", tmp)):
            text, seen = str(path), set()
            spellings = {"": text, "/": text.replace("\\", "/"), "j": json.dumps(text)[1:-1],
                         "q": quote(text), "Q": quote(text, safe="")}  # fmt: skip
            for kind, spelled in spellings.items():
                if spelled not in seen:
                    seen.add(spelled)
                    found.append((spelled.encode("utf-8"), f"<{word}{kind}>".encode()))
        self.spelled = sorted(found, key=lambda pair: -len(pair[0]))

    def plain(self, data: bytes, ports: bool = True) -> bytes:
        """``data`` without the places (and without the ports of this computer's servers)."""
        for spelled, word in self.spelled:
            data = data.replace(spelled, word)
        return PORT.sub(rb"\1<port>", data) if ports else data

    def filled(self, data: bytes, mine: bytes = b"") -> bytes:
        """Plain ``data`` with the places of this run. The ports it held are not kept: they
        become those of the Rust app's own answer ``mine``, one for one, where it holds as
        many (the port of a server of the test's is the same for both, and a server's own is
        told apart by no test), else a number as long as a port."""
        for spelled, word in self.spelled:
            data = data.replace(word, spelled)
        if b"<port>" in data:
            ports = [found.group(2) for found in PORT.finditer(mine)]
            if len(ports) != data.count(b"<port>"):
                ports = []
            for port in ports or [b"50000"] * data.count(b"<port>"):
                data = data.replace(b"<port>", port, 1)
        return data


def kept(data: bytes, whole: bool = False) -> Any:
    """Plain bytes as they are kept: text as it is, other bytes in base64, much of either as
    its digest (but ``whole``)."""
    if len(data) > WHOLE and not whole:
        return {"sha256": hashlib.sha256(data).hexdigest()[:16], "bytes": len(data)}
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(data).decode("ascii")}


def _same(form: Any, data: bytes) -> bool:
    """Whether plain ``data`` is what was kept as ``form``."""
    return kept(data, whole=isinstance(form, str) or "base64" in form) == form


def _bytes(form: Any) -> bytes:
    if isinstance(form, str):
        return form.encode("utf-8")
    if "base64" in form:
        return base64.b64decode(form["base64"])
    return f"<kept: {form['bytes']} bytes, sha256 {form['sha256']}>".encode()


def asked(places: Places, request: str | bytes, done: bool, left: bool) -> str:
    """A request as an answer is kept under: its digest, without the places."""
    text = request if isinstance(request, bytes) else request.encode("utf-8")
    flags = b"" if done and not left else f" done={done} left={left}".encode()
    return hashlib.sha256(places.plain(text) + flags).hexdigest()[:12]


def entry(places: Places, request: str | bytes, done: bool, left: bool, answer: Answer,
          whole: bool = False) -> dict[str, Any]:  # fmt: skip
    """An answer of the Python app's server as it is kept; ``whole``: its body however large
    (one the Rust app's answer is not: a picture a level apart here and there, readings of the
    moment, which a test looks into)."""
    status, headers, body = answer
    plain = {}
    for name, value in headers.items():
        if name.lower() == "content-length" and value == str(len(body)):
            plain[name] = "<len>"
        else:
            plain[name] = places.plain(value.encode("latin-1")).decode("latin-1")
    return {"asked": asked(places, request, done, left), "status": status, "headers": plain,
            "body": kept(places.plain(body), whole)}  # fmt: skip


def tree(home: Path) -> dict[str, tuple[int, int] | None]:
    """The files of a settings folder, each with its size and when it was written, and its
    folders (their names end in "/")."""
    found: dict[str, tuple[int, int] | None] = {}
    for path in sorted(home.rglob("*")):
        name = path.relative_to(home).as_posix()
        try:
            if path.is_dir():
                found[name + "/"] = None
            else:
                stat = path.stat()
                found[name] = (stat.st_size, stat.st_mtime_ns)
        except OSError:  # (gone since it was listed)
            continue
    return found


class Played:
    """The kept answers of a test (``entries``), played to its requests in their order; the
    Python server's settings folder (``homes[0]``) becomes what it was after each."""

    def __init__(self, entries: list[dict[str, Any]], homes: list[Path], tmp: Path) -> None:
        self.entries, self.homes, self.tmp, self.at = entries, homes, tmp, 0
        self.theirs, self.ours = Places(homes[0], tmp), Places(homes[1], tmp)
        self.last: dict[str, Any] | None = None
        self.again = False

    def shares(self, folder: Path) -> None:
        """A folder both servers write into (the pictures a person keeps): what the Python
        server wrote there is put there before the Rust app's is asked, as it was then."""

    def _lay_shared(self, changes: dict[str, Any] | None) -> None:
        """What the Python server wrote into a folder it shares, by the files' places in the
        test's folder."""
        for name, form in (changes or {}).items():
            target = self.tmp / name
            if form is None:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            elif name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(self.theirs.filled(_bytes(form)))

    def _as_theirs(self, form: Any, mine: bytes | None, now: bytes | None = None) -> bytes | None:
        """What was kept as ``form``, as the Python server had it in this run: made of what the
        Rust app's has (``mine``) where that is the same; None where it is there already
        (``now``)."""
        if mine is not None and _same(form, self.ours.plain(mine)):
            return self.theirs.filled(self.ours.plain(mine, ports=False))
        if now is not None and _same(form, self.theirs.plain(now)):
            return None
        return self.theirs.filled(_bytes(form), mine or b"")

    def _lay(self, changes: dict[str, Any] | None) -> None:
        """What the Python server's folder became, by its files: each as it was kept, or gone."""
        for name, form in (changes or {}).items():
            target, mine = self.homes[0] / name, self.homes[1] / name
            if form is None:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            elif name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                made = self._as_theirs(
                    form,
                    mine.read_bytes() if mine.is_file() else None,
                    target.read_bytes() if target.is_file() else None,
                )
                if made is not None:
                    target.write_bytes(made)

    def before(self, request: str | bytes, done: bool, left: bool) -> None:
        """The request that is asked next: the kept answer it gets is found."""
        digest, at, last = asked(self.theirs, request, done, left), self.at, self.last
        if at < len(self.entries) and self.entries[at]["asked"] == digest:
            pass
        elif last is not None and last["asked"] == digest:
            self.again = True  # (asked once more than when the answers were kept)
            return
        else:
            while (last is not None and at < len(self.entries)
                   and self.entries[at]["asked"] == last["asked"]):  # fmt: skip
                self._lay(self.entries[at].get("before"))
                self._lay_shared(self.entries[at].get("shared"))
                self._lay(self.entries[at].get("home"))
                at += 1
            assert at < len(self.entries), f"more is asked than was kept: {request[:300]!r}"
            assert self.entries[at]["asked"] == digest, (
                f"request {at} is another than when the answers were kept: {request[:300]!r}"
            )
        self.at, self.last, self.again = at + 1, self.entries[at], False
        self._lay(self.last.get("before"))
        self._lay_shared(self.last.get("shared"))

    def answer(self, ours: Answer) -> Answer:
        """The Python server's answer to the request, with the Rust app's at hand."""
        assert self.last is not None
        made = played(self.last, self.theirs, self.ours, ours)
        if not self.again:
            self._lay(self.last.get("home"))
        return made

    def left(self) -> int:
        """How many kept answers were not asked for (but those to a request asked again and
        again)."""
        rest = self.entries[self.at :]
        if self.last is not None:
            while rest and rest[0]["asked"] == self.last["asked"]:
                rest = rest[1:]
        return len(rest)


def played(made: dict[str, Any], theirs: Places, ours: Places, mine: Answer) -> Answer:
    """A kept answer as the Python server gave it in this run, with the Rust app's at hand."""

    def as_theirs(form: Any, own: bytes | None) -> bytes:
        if own is not None and _same(form, ours.plain(own)):
            return theirs.filled(ours.plain(own, ports=False))
        return theirs.filled(_bytes(form), own or b"")

    body = as_theirs(made["body"], mine[2])
    headers = {}
    for name, value in made["headers"].items():
        if value == "<len>":
            headers[name] = str(len(body))
        else:
            own = mine[1].get(name, "").encode("latin-1")
            if ours.plain(own).decode("latin-1") == value:
                said = theirs.filled(ours.plain(own, ports=False))
            else:
                said = theirs.filled(value.encode("latin-1"), own)
            headers[name] = said.decode("latin-1")
    return made["status"], headers, body
