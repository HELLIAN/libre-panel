"""The answers the comparisons got from lp-check while the Python app still checked them, kept
(docs/RUST.md, R12 the switch): each answer, once it was the same as the Python app's, as a
digest in tests/golden/<system>/<test file>.json, by test, call and case. A comparison that no
longer has the Python app to ask checks lp-check's answers against them (``rust_check.run``
does, for every test that kept some).

``LP_GOLDEN=record`` keeps the answers of the tests that run (the Python app's comparisons
passing, or else what the Rust app answers now, for a change meant to be seen): a test's
kept answers are written anew as a whole, the other tests' stay. ``LP_GOLDEN_OUT`` names a
folder where the answers of the tests that passed are written as well, while they are checked
as always (CI keeps them as a file of its run, golden-<system>).

What depends on where a test ran is taken out first: its temporary folder, the app's files,
the repository (each a word in angle brackets, with the path's separators as "/")."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

FOLDER = (
    Path(__file__).resolve().parent / "golden" / ("win32" if sys.platform == "win32" else "linux")
)
RECORDING = os.environ.get("LP_GOLDEN") == "record"
OUT = os.environ.get("LP_GOLDEN_OUT") or None

_places: list[tuple[str, str]] = []  # (a path as it is written, its word), longest first
_test: dict[str, Any] = {"file": None, "name": None, "calls": 0, "made": None}
_files: dict[str, dict[str, Any]] = {}  # the kept answers by test file, as read or recorded
_out: dict[str, dict[str, Any]] = {}  # the answers written to OUT by test file


def places(**named: Path | str) -> None:
    """The paths to write as words in the answers (``tmp``, ``data``, ``repo``), in every spelling
    an answer may have them in (as given, resolved, with "\\" or "/", JSON-escaped)."""
    found = []
    for word, path in named.items():
        for spelled in {str(path), str(Path(path).resolve())}:
            for each in {spelled, spelled.replace("\\", "/"), json.dumps(spelled)[1:-1]}:
                found.append((each, f"<{word}>"))
    _places[:] = sorted(set(found), key=lambda pair: -len(pair[0]))


def _plain_text(value: str) -> str:
    for spelled, word in _places:
        if spelled and spelled in value:
            start = value.find(spelled)
            while start != -1:
                end = start + len(spelled)
                # (the rest of that path with "/" between its parts)
                tail = re.match(r"[^\s\"'():,;]*", value[end:])
                rest = tail.group(0).replace("\\", "/") if tail else ""
                value = value[:start] + word + rest + value[end + len(rest) :]
                start = value.find(spelled, start + len(word))
    return value


def _plain(value: Any) -> Any:
    """The answer with the places taken out of its texts and keys; without calling itself, as
    an answer nests as deep as a reader lets it (a TOML value about 500 levels)."""
    holder = [value]
    todo: list[tuple[Any, Any]] = [(holder, 0)]  # (where an item is, its index or key)
    while todo:
        into, at = todo.pop()
        item = into[at]
        if isinstance(item, str):
            into[at] = _plain_text(item)
        elif isinstance(item, list):
            into[at] = made = list(item)
            todo.extend((made, n) for n in range(len(made)))
        elif isinstance(item, dict):
            into[at] = made = {
                (_plain_text(key) if isinstance(key, str) else key): each
                for key, each in item.items()
            }
            todo.extend((made, key) for key in made)
    return holder[0]


def digest(value: Any) -> str:
    """A short digest of an answer as JSON (keys sorted), the places taken out."""
    text = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _kept(file: str) -> dict[str, Any]:
    if file not in _files:
        path = FOLDER / f"{file}.json"
        _files[file] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return _files[file]


def begin(file: str, name: str) -> None:
    """A test begins (its file's name without .py, its name with its parameters)."""
    _test.update(file=file, name=name, calls=0, made=[] if RECORDING or OUT else None)


def has_kept() -> bool:
    """Whether answers were kept for the test that runs."""
    return _test["file"] is not None and _test["name"] in _kept(_test["file"])


def unasked() -> int:
    """How many of the calls kept for the test that runs it has not made (none: all were)."""
    kept = _kept(_test["file"]).get(_test["name"]) if _test["file"] and not RECORDING else None
    return 0 if kept is None else max(0, len(kept) - _test["calls"])


def _write(folder: Path, tests: dict[str, Any], file: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(sorted(tests.items())), indent=0, ensure_ascii=False)
    (folder / f"{file}.json").write_text(text + "\n", encoding="utf-8")


def end(passed: bool) -> None:
    """A test ended: what it recorded is kept where it passed, and written."""
    file, name, made = _test["file"], _test["name"], _test["made"]
    _test.update(file=None, name=None, calls=0, made=None)
    if file is None or not passed or made is None:
        return
    if OUT and made:
        _out.setdefault(file, {})[name] = made
        _write(Path(OUT) / FOLDER.name, _out[file], file)
    if not RECORDING:
        return
    tests = _kept(file)
    if not made and name not in tests:
        return
    if made:
        tests[name] = made
    else:
        tests.pop(name, None)
    _write(FOLDER, tests, file)


def answered(answers: list[Any]) -> None:
    """lp-check's answers to a call of the test: recorded, or checked against those kept."""
    if _test["file"] is None:
        return
    call, digests = _test["calls"], [digest(answer) for answer in answers]
    _test["calls"] += 1
    if _test["made"] is not None:
        _test["made"].append(digests)
    if RECORDING:
        return
    kept = _kept(_test["file"]).get(_test["name"])
    if kept is None:
        return
    assert call < len(kept), f"call {call} of {_test['name']}: none was kept"
    differ = [n for n, (ours, theirs) in enumerate(zip(digests, kept[call], strict=False))
              if ours != theirs]  # fmt: skip
    assert len(digests) == len(kept[call]), (call, len(digests), len(kept[call]))
    assert not differ, f"call {call}: cases {differ[:20]} answer otherwise than they did"
