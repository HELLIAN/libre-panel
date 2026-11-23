"""A "Share a theme" issue made into a community theme (.github/workflows/community-theme.yml).

GitHub's issue form (.github/ISSUE_TEMPLATE/share_theme.yml) brings the theme's .zip as an
attachment, and its name, its maker and what it shows as text; Libre Panel's *Share with
everyone…* fills the text in and saves the zip. The workflow runs this in two steps, in two
jobs, so that what comes from the issue is looked into only where nothing can be written:

- ``check`` (no rights): downloads the zip from GitHub's attachments (nowhere else), checks it
  as Libre Panel checks every shared theme (rust/app/src/share.rs: plain JSON, pictures and fonts,
  nothing runs), works out the panel size it is made for, draws its picture with the demo
  readings (none of anybody's computer) and leaves the zip, the picture and the list's entry
  in a folder; or, where something is wrong, the reason in words.
- ``add`` (the job that may write): takes that folder, checks the entry as the app reads the
  list (rust/app/src/community.rs), the zip against its SHA-256 and the picture by its first bytes,
  and puts them beside the list, ``community/themes/<id>/``, with the entry in index.json; a
  new version of a listed theme (the form's *Replaces*) takes that one's place, its folder and
  its entry. The workflow opens a pull request with them; a person looks at it before it is
  merged.

Text from the issue is data: it reaches no shell, and the entry keeps it within the list's
lengths.

What Libre Panel checks of a shared theme, how it draws it and how it reads the list is the
program's own (`libre-panel share` and `libre-panel preview`): LIBRE_PANEL_PROGRAM names it,
else it is the `libre-panel` on the PATH.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

FORM_FIELDS = {  # the issue form's labels: the text under each "### <label>"
    "Name": "name",
    "Replaces": "replaces",
    "Made by": "author",
    "What it shows": "description",
    "The theme": "theme",
    "Panels you tried it on": "tried",
    "Sharing": "consent",
}
NO_ANSWER = "_No response_"
# GitHub's attachments, and where they lead
ATTACHMENT = re.compile(
    r"https://github\.com/(?:user-attachments/files|syntensa/libre-panel/files)/\d+/[^\s)\]>\"']+\.zip"
)
HOSTS = ("github.com", "objects.githubusercontent.com")
S3 = re.compile(r"^github-production-[a-z0-9-]+\.s3\.amazonaws\.com$")
LIST = Path("community/themes")
TEXTS = {"name": 80, "replaces": 80, "author": 80, "description": 400}
BRANCH = "community/theme-"  # and the issue's number: the pull request's branch (the workflow's)
TIMEOUT_S = 30
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")  # a theme's folder (theme/model.py)


class Refused(ValueError):
    """What keeps the theme out, said to the person who shared it."""


class ShareError(ValueError):
    """What Libre Panel says of a shared theme it does not take, in its words."""


# -- the program --------------------------------------------------------------------------------


def program() -> str:
    """The Libre Panel that checks and draws (LIBRE_PANEL_PROGRAM, else the one on the PATH)."""
    found = os.environ.get("LIBRE_PANEL_PROGRAM") or shutil.which("libre-panel")
    if not found:
        raise RuntimeError("needs the libre-panel program: LIBRE_PANEL_PROGRAM or the PATH")
    return found


def _run(*args: str, home: Path | None = None) -> str:
    """What the program prints for ``args``; :class:`ShareError` with what it said where it
    refused. ``home``: its own folder of settings (a run that leaves nothing behind)."""
    with tempfile.TemporaryDirectory() as place:
        env = {**os.environ, "LIBRE_PANEL_HOME": str(home or place)}
        done = subprocess.run([program(), *args], capture_output=True, text=True, env=env,
                              encoding="utf-8", timeout=120)  # fmt: skip
    if done.returncode == 1:
        raise ShareError(done.stderr.strip() or done.stdout.strip())
    if done.returncode != 0:
        raise RuntimeError(f"libre-panel {args[0]} ended with {done.returncode}: {done.stderr}")
    return done.stdout


@functools.cache
def limits() -> dict[str, Any]:
    """The largest zip, list and picture, and the list's format, as the app has them."""
    return json.loads(_run("share", "limits"))


def _with_zip(data: bytes, *args: str) -> str:
    """``libre-panel share`` of a zip given as bytes (in a file of its own meanwhile)."""
    with tempfile.TemporaryDirectory() as place:
        zip_file = Path(place) / "theme.zip"
        zip_file.write_bytes(data)
        return _run("share", args[0], str(zip_file), *args[1:])


def inspect_shared(data: bytes) -> dict[str, Any]:
    """What a shared theme's zip holds, checked as the app checks it on import: its name, size,
    maker and licence; :class:`ShareError` where the app does not take it."""
    return json.loads(_with_zip(data, "inspect"))


def retitled(data: bytes, **given: str) -> bytes:
    """The zip with ``given`` set in its theme.json (name, author, description; one left empty
    stays as it is), every other file as it was."""
    with tempfile.TemporaryDirectory() as place:
        out = Path(place) / "retitled.zip"
        options = [part for key, value in given.items() for part in (f"--{key}", value)]
        _with_zip(data, "retitle", str(out), *options)
        return out.read_bytes()


# -- the issue --------------------------------------------------------------------------------


def fields(body: str) -> dict[str, str]:
    """The answers of the issue form, by field: the text under each heading it knows."""
    found: dict[str, list[str]] = {}
    current = None
    for line in body.replace("\r\n", "\n").split("\n"):
        heading = re.match(r"^###\s+(.+?)\s*$", line)
        if heading:
            current = FORM_FIELDS.get(heading[1])
            if current is not None:
                found[current] = []
            continue
        if current is not None:
            found[current].append(line)
    answers = {}
    for key, lines in found.items():
        text = "\n".join(lines).strip()
        answers[key] = "" if text == NO_ANSWER else text
    return answers


def attachment(text: str) -> str:
    """The address of the one .zip attached."""
    found = list(dict.fromkeys(ATTACHMENT.findall(text)))
    if not found:
        raise Refused(
            "There is no .zip under *The theme*. Drag the file Libre Panel saved (it ends in "
            "`.zip`) into that field, then edit the issue: the check runs again by itself."
        )
    if len(found) > 1:
        raise Refused("There is more than one .zip under *The theme*: one theme an issue, please.")
    return found[0]


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and (host in HOSTS or bool(S3.match(host)))


class _GitHubOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            return None  # (urllib then answers with the redirect as an error)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, limit: int) -> bytes:
    """The attachment's bytes, from GitHub only, at most ``limit`` of them."""
    if not _allowed(url):
        raise Refused("The .zip is not an attachment of this issue.")
    request = urllib.request.Request(url, headers={"User-Agent": "libre-panel-community"})
    try:
        with urllib.request.build_opener(_GitHubOnly).open(request, timeout=TIMEOUT_S) as answer:
            data = answer.read(limit + 1)
    except OSError as exc:
        raise Refused(f"The .zip could not be downloaded ({exc}). Attach it again?") from exc
    if len(data) > limit:
        raise Refused(f"The .zip is larger than {limit // (1024 * 1024)} MB.")
    return data


# what a form's answer may hold besides text: a picture pasted into it (as GitHub writes it: an
# HTML <img>, or Markdown's ![…](…)), other HTML
PASTED = re.compile(r"!\[[^\]]*\]\([^)]*\)|<[^>]*>")


def _plain(text: str) -> str:
    """An answer's text on one line, without what was pasted into it."""
    return " ".join(PASTED.sub(" ", text).split())


def _text(answers: dict[str, str], key: str, fallback: str) -> tuple[str, bool]:
    """A text of the entry within its length (cut at a word, said), else ``fallback``; a
    picture pasted into the answer is left out (#27: one under *Made by* was in the maker)."""
    text = _plain(answers.get(key) or "") or fallback
    most = TEXTS[key]
    if len(text) <= most:
        return text, False
    return text[: most - 1].rsplit(" ", 1)[0].rstrip(".,;: ") + "…", True


def _maker(given: str, original: str) -> str:
    """Who made the theme, as the form says (else as the theme says), with the maker the theme
    names where that is someone else: a theme made from another one keeps its credit. The same
    name written otherwise ("Varulfen-3D-Designs" for "Varulfen 3D Designs", #34) is no one
    else."""
    given, original = _plain(given), " ".join(original.split())

    def bare(name: str) -> str:
        return "".join(ch for ch in name.casefold() if ch.isalnum())

    if not given or not original or bare(original) in bare(given):
        return given or original
    return f"{given}, after {original}"


# -- check (no rights) -------------------------------------------------------------------------


def _picture(data: bytes) -> bytes:
    """The theme drawn with the demo readings, as a PNG (`libre-panel preview`)."""
    with tempfile.TemporaryDirectory() as place:
        themes, out = Path(place) / "themes", Path(place) / "preview.png"
        theme_id = _with_zip(data, "import", str(themes)).strip()
        _run("preview", str(themes / theme_id), "-o", str(out))
        return out.read_bytes()


def check(body: str, login: str, out: Path, get=download) -> dict[str, Any]:
    """The theme of an issue checked and drawn: its zip, picture and entry in ``out``
    (``theme.zip``, ``preview.png``, ``entry.json``). :class:`Refused` where it cannot go in."""
    answers = fields(body)
    if "[x]" not in answers.get("consent", "").lower():
        raise Refused("Please tick that every picture and font in the theme may be shared.")
    data = get(attachment(answers.get("theme", "")), limits()["max_zip"])
    try:
        theme = inspect_shared(data)
    except ShareError as exc:
        raise Refused(f"Libre Panel cannot take this theme: {exc}") from exc
    maker = _maker(answers.get("author", ""), theme["author"] or "")
    name, cut_name = _text(answers, "name", theme["name"])
    author, cut_author = _text({"author": maker}, "author", login)
    description, cut_description = _text(answers, "description", "")
    try:  # installed, it is called what its entry says (its maker in full, with whom it is after)
        data = retitled(data, name=name, author=maker, description=description)
        inspect_shared(data)
        picture = _picture(data)
    except ShareError as exc:
        raise Refused(f"Libre Panel cannot take this theme: {exc}") from exc
    if len(picture) > limits()["max_preview"]:
        raise Refused("Its picture comes out larger than the list takes; fewer photos, perhaps?")
    width, height = theme["size"]
    sha256 = hashlib.sha256(data).hexdigest()
    entry = {"name": name, "author": author, "description": description,
             "sizes": [f"{width}x{height}"], "sha256": sha256}  # fmt: skip
    replaces = _text(answers, "replaces", "")[0]
    if replaces:  # (which listed theme it is: the job that adds it looks)
        entry["replaces"] = replaces
    out.mkdir(parents=True, exist_ok=True)
    (out / "theme.zip").write_bytes(data)
    (out / "preview.png").write_bytes(picture)
    (out / "entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                                    encoding="utf-8")  # fmt: skip
    cut = [what for what, was in (("name", cut_name), ("maker", cut_author),
                                  ("description", cut_description)) if was]  # fmt: skip
    return {**entry, "cut": cut, "tried": answers.get("tried", "")}


# -- add (the job that may write) --------------------------------------------------------------


def _built_in() -> set[str]:
    """The ids of the themes that come with Libre Panel (`libre-panel themes`)."""
    listed = _run("themes").splitlines()
    return {line.split()[0] for line in listed if len(line.split()) > 1}


def _taken(root: Path) -> set[str]:
    index = json.loads((root / LIST / "index.json").read_text(encoding="utf-8"))
    names = {Path(entry.get("zip", "")).stem for entry in index.get("themes", [])}
    names |= {path.name for path in (root / LIST).iterdir() if path.is_dir()}
    return names | _built_in()  # (not to hide a built-in one)


def theme_id(name: str, number: int, taken: set[str]) -> str:
    """The entry's id: its folder, its zip's name, its folder where it is installed."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48].strip("-")
    base = slug if NAME.match(slug) and ".." not in slug else f"theme-{number}"
    chosen, n = base, 1
    while chosen in taken:
        n += 1
        chosen = f"{base}-{n}"
    return chosen


def _same_name(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


def replaced(index: dict[str, Any], name: str) -> tuple[int, str]:
    """The listed theme a new version takes the place of: its place in the list and its id,
    by its name as the list shows it (or its id)."""
    found = [(at, Path(str(entry.get("zip", ""))).stem)
             for at, entry in enumerate(index.get("themes", []))
             if _same_name(str(entry.get("name", "")), name)
             or Path(str(entry.get("zip", ""))).stem == name.strip()]  # fmt: skip
    if not found:
        raise Refused(
            f"No theme called “{name}” is among the community themes. Write its name under "
            "*Replaces* as *Start → Themes → Community themes* shows it, or leave the field empty "
            "for a new theme."
        )
    if len(found) > 1:
        raise Refused(f"More than one community theme is called “{name}”: write its id instead.")
    return found[0]


def add(checked: Path, number: int, root: Path) -> dict[str, Any]:
    """The checked theme beside the list, with its entry in index.json: the entry as written;
    a new version of a listed theme in that one's place (its folder, its entry, where it was in
    the list), with what it replaces under ``replaced``. What ``check`` left is read as data
    again: the entry as the app reads the list, the zip by its SHA-256, the picture by its first
    bytes."""
    raw = json.loads((checked / "entry.json").read_text(encoding="utf-8"))
    data, picture = (checked / "theme.zip").read_bytes(), (checked / "preview.png").read_bytes()
    if len(data) > limits()["max_zip"] or hashlib.sha256(data).hexdigest() != raw.get("sha256"):
        raise Refused("The checked zip is not the one its entry names.")
    if len(picture) > limits()["max_preview"] or not picture.startswith(b"\x89PNG\r\n\x1a\n"):
        raise Refused("The checked picture is no PNG of the size the list takes.")
    index_path = root / LIST / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    replaces = " ".join(str(raw.get("replaces") or "").split())
    if replaces:
        at, chosen = replaced(index, replaces)
        was = index["themes"][at]
    else:
        at, chosen, was = None, theme_id(str(raw.get("name", "")), number, _taken(root)), None
    entry = {key: raw.get(key) for key in ("name", "author", "description", "sizes")}
    entry.update(preview=f"{chosen}/preview.png", zip=f"{chosen}/{chosen}.zip",
                 sha256=raw.get("sha256"))  # fmt: skip
    if not _readable({"format": limits()["format"], "themes": [entry]}):
        raise Refused("The entry is not one the app can read.")
    folder = root / LIST / chosen
    if was is not None:  # (what the version before had there goes: the folder is this one's)
        shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / f"{chosen}.zip").write_bytes(data)
    (folder / "preview.png").write_bytes(picture)
    if at is None:
        index["themes"].append(entry)
    else:
        index["themes"][at] = entry
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if was is None:
        return {**entry, "id": chosen}
    before = {key: was.get(key) for key in ("name", "author", "sizes")}
    return {**entry, "id": chosen, "replaced": before}


def _readable(index: dict[str, Any]) -> int:
    """How many entries of a list the app reads (`libre-panel share entry`)."""
    with tempfile.TemporaryDirectory() as place:
        listed = Path(place) / "index.json"
        listed.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        try:
            return int(_run("share", "entry", str(listed)))
        except ShareError:
            return 0


def _sizes(sizes: list[str]) -> str:
    return ", ".join(size.replace("x", " × ") for size in sizes)


def pull_request_text(entry: dict[str, Any], number: int) -> str:
    """What the pull request says: the entry, from where it came, what to look at; for a new
    version, which listed theme it takes the place of and what to make sure of first."""
    sizes = _sizes(entry["sizes"])
    was = entry.get("replaced")
    if was is None:
        head = [f"A community theme shared in #{number}: **{entry['name']}** by {entry['author']}, "
                f"made for {sizes}."]  # fmt: skip
    else:
        head = [
            f"A new version of the community theme **{was['name']}** by {was['author']} (made for "
            f"{_sizes(was['sizes'])}), shared in #{number}: **{entry['name']}** by "
            f"{entry['author']}, made for {sizes}.",
            "",
            f"It takes that one's place: its folder (`community/themes/{entry['id']}/`) and its "
            "entry in index.json, where it was in the list; the app installs this version from "
            "then on. **Before merging, make sure it is theirs to replace:** that the one who "
            f"opened #{number} is the one who shared the version before (`git log -- "
            f"community/themes/{entry['id']}`), or that its maker agrees.",
        ]  # fmt: skip
    return "\n".join([
        *head,
        "",
        f"> {entry['description']}" if entry["description"] else "> (no description)",
        "",
        f"![{entry['name']}](https://github.com/syntensa/libre-panel/blob/{BRANCH}{number}/"
        f"community/themes/{entry['preview']}?raw=true)",
        "",
        "Checked by the workflow as Libre Panel checks every shared theme, and drawn with the "
        "demo readings; its theme.json carries the name, maker and description above. Before "
        "merging, look that it shows what its entry says, that nothing "
        "in it should not be shared, and that its license is named.",
        "",
        f"Its commit is by the one who opened #{number}: merge with **Rebase and merge** (or a "
        "merge commit), which keeps it as it is, so they show among the contributors.",
        "",
        "No test run belongs to this pull request (the check above is its test): it can be "
        "merged as it is. Where another theme is merged first, this one is made anew on the "
        "new list by itself, within a few minutes.",
        "",
        f"Closes #{number}",
    ])  # fmt: skip


# -- the command line ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="step", required=True)
    one = sub.add_parser("check", help="check and draw the theme of an issue (no rights)")
    one.add_argument("--issue", type=Path, required=True, help="the issue's text, in a file")
    one.add_argument("--login", required=True, help="who opened the issue")
    one.add_argument("--out", type=Path, required=True, help="the folder for what is checked")
    two = sub.add_parser("add", help="put a checked theme beside the list")
    two.add_argument("--checked", type=Path, required=True)
    two.add_argument("--number", type=int, required=True, help="the issue's number")
    two.add_argument("--root", type=Path, default=Path("."))
    two.add_argument("--pull-request", type=Path, required=True, help="its text, written here")
    two.add_argument("--refusal", type=Path, required=True, help="what to say, where refused")
    args = parser.parse_args(argv)
    if args.step == "check":
        try:
            checked = check(args.issue.read_text(encoding="utf-8"), args.login, args.out)
        except Refused as exc:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "refused.md").write_text(f"{exc}\n", encoding="utf-8")
            print(f"refused: {exc}")
            return 0  # (the next job says it on the issue)
        replacing = f", in the place of {checked['replaces']}" if checked.get("replaces") else ""
        print(f"checked: {checked['name']} for {', '.join(checked['sizes'])}{replacing}")
        return 0
    refused = args.checked / "refused.md"
    try:
        if refused.exists():
            raise Refused(refused.read_text(encoding="utf-8").strip())
        entry = add(args.checked, args.number, args.root)
    except Refused as exc:
        args.refusal.write_text(
            f"{exc}\n\nEdit the issue to try again: the check runs by itself.\n", encoding="utf-8"
        )
        return 2
    args.pull_request.write_text(pull_request_text(entry, args.number) + "\n", encoding="utf-8")
    # (what the commit and the pull request are called)
    print(entry["id"] + (" (new version)" if "replaced" in entry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
