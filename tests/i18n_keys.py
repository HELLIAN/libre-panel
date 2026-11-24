"""Collect every text Libre Panel shows through its translation layer.

Used by test_i18n.py and test_locales.py: every collected text must have a translation, and a
catalog must not carry texts nobody uses any more. The texts are the Rust app's (each
``t("...")``, ``tf("...", ...)`` and ``t_format("...", ...)`` in rust/, and the tables it
translates from, which lp-check tells), the plugin host's (src/libre_panel), the pages' scripts
and the built-in themes'.
"""

from __future__ import annotations

import ast
import functools
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "libre_panel"
RUST = ROOT / "rust"
STATIC = ROOT / "data" / "editor" / "static"  # what the console's and plugins' pages share
PAGES = ROOT / "data" / "pages" / "web"  # the scripts of the built-in pages
APP = ROOT / "data" / "editor" / "app"  # the app's modules; not its vendored libraries

# a Rust string literal (raw ones are made plain first: _plain_raw)
_RUST_STRING = re.compile(r'(?<![\w#])"((?:[^"\\]|\\.)*)"', re.S)
_RUST_RAW = re.compile(r'(?<![\w"])r(#*)"(.*?)"\1', re.S)
# a call that translates the text it is given: lp_i18n's t and t_format, and the crates' tf
_RUST_CALL = re.compile(r'(?<![\w])(?:t|tf|t_format)\(\s*"((?:[^"\\]|\\.)*)"', re.S)
_RUST_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}
_JS_CALL = re.compile(r"""\bt\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
_JS_TEMPLATE_CALL = re.compile(r"\bt\(\s*`")


def python_messages() -> set[str]:
    found = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            first = node.args[0]
            if name == "t" and isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def _rust_text(literal: str) -> str:
    """The text of a Rust string literal's inside: its escapes, its line continuations."""
    literal = re.sub(r"\\\n\s*", "", literal)
    out, at = [], 0
    while at < len(literal):
        char = literal[at]
        if char == "\\" and at + 1 < len(literal):
            code = literal[at + 1]
            if code == "u" and literal[at + 2 : at + 3] == "{":
                end = literal.index("}", at)
                out.append(chr(int(literal[at + 3 : end], 16)))
                at = end + 1
                continue
            out.append(_RUST_ESCAPES.get(code, code))
            at += 2
            continue
        out.append(char)
        at += 1
    return "".join(out)


def _plain_raw(source: str) -> str:
    """``r#"..."#`` written as an ordinary literal, so that one pattern reads both."""

    def plain(match: re.Match) -> str:
        return '"' + match.group(2).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return _RUST_RAW.sub(plain, source)


@functools.cache
def _rust_sources() -> tuple[str, ...]:
    return tuple(_plain_raw(path.read_text(encoding="utf-8"))
                 for path in sorted(RUST.glob("*/src/**/*.rs")))  # fmt: skip


def rust_messages() -> set[str]:
    """The texts the Rust app's code translates as it shows them."""
    return {
        _rust_text(m.group(1)) for source in _rust_sources() for m in _RUST_CALL.finditer(source)
    }


@functools.cache
def rust_literals() -> frozenset[str]:
    """Every text written in the Rust app's code: a text a catalog has is in use while one of
    these is it (a table's word, translated where it is shown)."""
    return frozenset(_rust_text(m.group(1)) for source in _rust_sources()
                     for m in _RUST_STRING.finditer(source))  # fmt: skip


@functools.cache
def _lp_check(op: str) -> dict:
    from rust_check import run

    (said,) = run([{"op": op}])
    return said


def script_files() -> list[Path]:
    """The scripts that show texts: those the built-in pages share, the pages' own and the
    app's (not its vendored libraries)."""
    app = (path for path in APP.rglob("*.js") if "vendor" not in path.relative_to(APP).parts)
    return sorted(STATIC.glob("*.js")) + sorted(PAGES.glob("*.js")) + sorted(app)


def js_messages() -> set[str]:
    found = set()
    for path in script_files():
        source = path.read_text(encoding="utf-8")
        if _JS_TEMPLATE_CALL.search(source):
            raise AssertionError(f't(`...`) in {path.name}: use t("... {{name}}", {{ name }})')
        for literal in _JS_CALL.findall(source):
            if literal.startswith("'"):
                literal = '"' + literal[1:-1].replace('\\"', '"').replace('"', '\\"') + '"'
                literal = literal.replace("\\'", "'")
            found.add(json.loads(literal))
    return found


def dynamic_messages() -> set[str]:
    """Texts translated from data tables rather than written as t("..."): the Rust app's
    (lp-check's i18n_texts: the weather, the moon, a battery's and a player's state, what a
    panel model's notes and limits say) and the editor's presets."""
    tables = _lp_check("i18n_texts")
    presets = json.loads((RUST / "app" / "src" / "presets.json").read_text(encoding="utf-8"))
    return (
        set(tables["weather"]) | set(tables["moon"]) | set(tables["battery"])
        | set(tables["media"]) | set(tables["notes"]) | set(tables["limits"])
        | {"Unknown"}  # (a weather code the table lacks)
        | {preset["name"] for preset in presets["presets"]}
    )  # fmt: skip


def theme_messages() -> set[str]:
    """The words of the built-in themes that are shown in the panel's language ("translate"),
    and what Look calls their switches."""
    found = set()
    for path in (ROOT / "data" / "themes").glob("*/theme.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for widget in data.get("widgets", []):
            if widget.get("type") == "text" and widget.get("translate"):
                found.add(widget["text"])
        found |= {spec["label"] for spec in data.get("switches", {}).values()}
    return found


@functools.cache
def all_messages() -> frozenset[str]:
    """Every text shown through the translation layer. Worked out once for the whole run (it reads
    every source file of the app: two or three seconds, and every language's test asks twice)."""
    return frozenset(rust_messages() | python_messages() | js_messages() | dynamic_messages()
                     | theme_messages())  # fmt: skip


def known_texts() -> frozenset[str]:
    """What a catalog may hold: every text shown through the translation layer, and every text
    the Rust app's code writes (a table's word, translated where it is shown)."""
    return all_messages() | rust_literals()


def tables() -> dict[str, set[str]]:
    """The editor's tables, as the Rust app has them (lp-check's theme_tables): the fields of
    every widget, of the toast styles (Theme -> Messages) and of the looks (Theme -> Look), the
    widget types, the words of their choices, and the icons."""
    said = json.loads(_lp_check("theme_tables")["tables"])
    common, widgets = said["COMMON"], said["WIDGET_SPECS"]
    fields, enums = set(common), set(said["TOAST_ANCHORS"])  # the editor's toast position
    specs = [
        common,
        *widgets.values(),
        *said["TOAST_STYLES"].values(),
        *said["LOOK_SPECS"].values(),
    ]
    for spec in specs:
        fields |= set(spec)
        for kind, _ in spec.values():
            kind = kind.rstrip("?")
            if kind.startswith("enum:"):
                enums |= set(kind[5:].split("|"))
    return {
        "fields": fields,
        "widgets": set(widgets),
        "enums": enums,
        "icons": set(said["ICON_NAMES"]),
    }


if __name__ == "__main__":  # list what the German catalog lacks
    import sys

    sys.path.insert(0, str(SRC.parent))
    catalog = json.loads((ROOT / "data" / "locale" / "de.json").read_text(encoding="utf-8"))
    for text in sorted(all_messages() - set(catalog.get("messages", {}))):
        print("messages:", json.dumps(text, ensure_ascii=False))
    for table, keys in tables().items():
        for key in sorted(keys - set(catalog.get(table, {}))):
            print(f"{table}:", key)
