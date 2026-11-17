"""The panel's own screens on a TURZX USB panel: its boot logo and its standby clip.

The panel shows a JPEG when it is powered on (``usr/data/boot.jpg``, else its factory logo,
which is never written) and plays an H.264 clip in a loop when the PC's USB comes up, until a
program streams (the name kept in its settings; the vendor app's ``usr/data/standby.h264``).
render/bootscreen.py draws both in Libre Panel's look; this puts them on the panel. What it
does comes from SPUR II's reading of the 8.8"'s firmware and its measurements on the 9.2":

- **40** writes a file: ``[8:12]`` the name's length, ``[12:16]`` the file's, ``[16:]`` the
  name; the file follows in the same transfer, at most 1 MiB. **42** deletes one, **98** says
  its size (little endian at ``[8:12]``), **99** lists a folder in 20 pieces of 512 bytes (all
  of them, or the next listing does not begin afresh), **113** shows a JPEG of the panel's.
- **15** sets the player's rate (a cold start sets 30), **110** plays a clip and keeps its name
  in the panel's settings in memory, **125** saves the settings: brightness, start mode (2: the
  clip), rotation, with the name 110 gave.

Its safeguards: only on the person's own action, never while the panel shows themes (the
app pauses it first); only the two files, by their names below ``usr/data/``. 12 (halt), 13
and 11 are never sent from here, and 110 only with the clip's real name (an empty one, saved,
would lose the standby clip for good).

**How much is written** (issue 52). ``usr/data`` holds the panel's own settings too
(``app.cfg``), a panel cannot be asked how much room it has there, and on one 9.2" that room
ran out at about 718,000 bytes of Libre Panel's files: a boot logo ended at 16 KiB, and for
five days that panel took no file and answered each write at once. Then a larger clip ended
early, 0.9.1 took it off and wrote it again, the settings were saved (125), and the panel has
not got through a start since. The files had grown with 0.7.9, which made them "as fine as
fits in 1 MiB", the most the command takes: up to 875,000 bytes for one look on a 9.2". So,
since 0.9.4 (:data:`ROOM`, :meth:`PanelFiles.write`):

- the clip and the logo are made for a room that is two thirds of what that 9.2" held (on
  the 12.3", four fifths of what one has held for weeks), and never coarser than shows
  (:data:`CLIP_CRF_MAX`, :data:`JPEG_STEPS`): a look that needs more is not written;
- before a file goes, the panel is asked for the sizes of both: one that is larger than the
  file it replaces is not sent where both together would be more than that room;
- after it, the size is asked again. A file the panel did not take (it has what it had) is
  said so, and nothing more is sent. One that ended early is taken off again at once (42), so
  that no panel is left full. Nothing is ever taken away in order to write it a second time;
- only the 9.2" and the 12.3" are written to, the models it has been done on, and the 8.8",
  which its maker sells as the 9.2"'s sibling, with its room; another needs
  ``LIBRE_PANEL_PANEL_WRITES=at-my-own-risk`` in the environment.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image

from libre_panel.devices.base import DeviceError
from libre_panel.devices.models import MODELS, PanelModel
from libre_panel.devices.turzx import VENDOR_CLIP
from libre_panel.devices.turzx_usb import (
    ACK,
    CMD_FRAME_RATE,
    USB_PIDS,
    UsbTransport,
    brightness_arg,
    to_native,
)
from libre_panel.i18n import t
from libre_panel.render import bootscreen

log = logging.getLogger(__name__)

FOLDER = "usr/data"
BOOT_LOGO = "usr/data/boot.jpg"
# the panels the boot picture and the standby clip were measured on (SPUR II on the 9.2");
# the other USB panels share its firmware but have not been tried
MEASURED = ("turing-9.2-usb",)
STANDBY_CLIP = VENDOR_CLIP  # the vendor app's name, which the video mode names too
OURS = (BOOT_LOGO, STANDBY_CLIP)  # the only files written or taken off
MAX_FILE = 1024 * 1024
LIST_PIECES = 20
PIECE = 512
# the boot logo's JPEG: its quality in turn until the file fits its room, every pixel with its
# own colour (a colour for four cost more than it saved: on Circuit's traces 38.7 dB where the
# same bytes gave 42.1 so). Every whole quality from 95 down, since 0.9.5: with steps of 92, 90,
# 88 the default look's logo was 92 on the 9.2" in 69 % of its room, and is 94 now in 91 %
# (0.75 dB finer). A look that would need less than the last would show it: not made
JPEG_STEPS = tuple(range(95, 84, -1))
# the standby clip's x264 crf: the finest first, then coarser until the clip fits its room, up to
# the coarsest that does not show; a look that needs more is not made. (Finer than CLIP_CRF only
# made the file larger: Circuit's clip on the 9.2" was 715 KB at crf 17 and is 395 at 20, 52.8
# and 51.4 dB.)
CLIP_CRF, CLIP_CRF_MAX = 20, 26
# below a whole step that fits, where the next finer one does not: these quarters of a step,
# made at once (each encoder has one thread), and the finest that fits is the clip. A whole
# step left up to 13 % of the room unused: Aurora's clip on end on the 9.2" was 313,126 bytes
# at 26 (46.8 dB) and is 347,820 at 25.25 (47.2), where 25 is 361,140. (Since 0.9.5)
CLIP_QUARTERS = (0.75, 0.5, 0.25)
# how much smaller a clip gets with each step of the crf, in thousandths (0.85 to 0.89 over
# the looks): the first guess at the crf that fits
CLIP_STEP = 870

# How much Libre Panel puts on a panel: the most bytes of the standby clip and of the boot logo
# (issue 52). Both go where the panel keeps its own settings; how much room there is cannot be
# asked, and is not the same on every panel. One 9.2" was full at about 718,000 bytes of these
# files, and what a panel does once it is full is what ended that panel. So the two together
# stay well below what a panel of the model is known to hold: on the 9.2" two thirds of those
# 718,000; on the 12.3" four fifths of the 817,587 one has held for weeks. (The files of
# 0.7.9 to 0.9.1 were up to 875,000 bytes for a look on a 9.2" and 1,150,000 on a 12.3".)
# The 8.8" gets the 9.2"'s: its maker sells the two as one, with the same framebuffer
# (480 x 1920), and the panel's own start screen was first read from the 8.8"'s firmware
# (SPUR II); written to an 8.8" in 0.9.5's candidate first, by an owner. The two together are
# what counts: the clip may take what its look's logo leaves (clip_room).
ROOM: dict[str, tuple[int, int]] = {
    "turing-8.8-usb": (360_000, 120_000),
    "turing-9.2-usb": (360_000, 120_000),
    "turing-12.3-usb": (500_000, 150_000),
}
# The looks that do not fit that room finely on a model, known before anything is made: the
# card says so and offers no writing. Levels is thousands of small lights: its boot logo alone
# is 491 KB on the 9.2" as finely as the others are made, and its clip 877.
UNFIT: dict[str, tuple[str, ...]] = {
    "turing-8.8-usb": ("levels",),
    "turing-9.2-usb": ("levels",),
    "turing-12.3-usb": ("levels",),
}
# A write that happens takes its time: the 9.2" of issue 52 took 1.2 to 2.9 s for 700 KB and
# 0.1 s for 48 KB, and a sound 9.2" and 12.3" wrote 45 to 485 KB at 0.94 MB a second at the
# most (0.9.4rc1). One that the panel does not take is over when the transfer is: 30 to 50 ms
# for 700 KB, 14 MB a second and more. So a file of some size that was answered faster than
# this many bytes a second was not written, which matters where the panel has a file of that
# very size already
WRITE_RATE_MOST = 4_000_000
# (below it the transfer's own time says nothing. On the 9.2" and the 12.3" every clip is
# larger: the smallest, Dark's on the 9.2", has about 44,000 bytes; only Dark's boot logos
# are smaller, and after a logo nothing is saved on the panel)
WRITE_TIMED_FROM = 32 * 1024

CMD_WRITE_FILE = 40
CMD_DELETE_FILE = 42
CMD_FILE_SIZE = 98
CMD_LIST = 99
CMD_PLAY_LOCAL = 110
CMD_SHOW_JPEG = 113
CMD_SAVE_SETTINGS = 125
START_WITH_CLIP = 2  # the settings' start mode: play the clip they name
NEVER = frozenset({11, 12, 13})  # restart, halt, and the other save of the settings

# Files are written to the models it has been done on: the 9.2" and the 12.3", and the 8.8" as
# the 9.2" (ROOM). How much room another panel of the family has is not known, so nothing is
# written to one. Who looks into it on a panel of their own sets this in the environment, at
# their own risk: the files are then made for the 9.2"'s room, by the pixels.
WRITES_ENV = "LIBRE_PANEL_PANEL_WRITES"
WRITES_WORD = "at-my-own-risk"


def tried(model: PanelModel | None) -> bool:
    """Whether start screens are written to panels of ``model`` by what is known of its room
    (:data:`ROOM`: the models it has been done on, and the 8.8" as the 9.2")."""
    return model is not None and model.id in ROOM


def room(model: PanelModel | None) -> tuple[int, int]:
    """The most bytes of the standby clip and of the boot logo on a panel of ``model``: what
    :data:`ROOM` says for the models written to; for another (and for none), the 9.2"'s for as
    many pixels as it has, and never more."""
    clip, logo = ROOM["turing-9.2-usb"]
    if model is None:
        return clip, logo
    if model.id in ROOM:
        return ROOM[model.id]
    most = 1920 * 480
    share = min(model.native_width * model.native_height, most)
    return clip * share // most, logo * share // most


def writes_on(model: PanelModel | None = None) -> bool:
    """Whether files are written to a panel of ``model``: to the models it has been done on,
    and to any for who asks for it in the environment
    (``LIBRE_PANEL_PANEL_WRITES=at-my-own-risk``)."""
    return tried(model) or os.environ.get(WRITES_ENV) == WRITES_WORD


def writes_off() -> str:
    """Why nothing is written to a panel of another model, as the app says it."""
    return t(
        'Writing to this panel is switched off: Libre Panel writes start screens to the 8.8", '
        'the 9.2" and the 12.3" only, and how much room another panel has for them is not known.'
    )


def too_fine() -> str:
    """Why a look is not made for a panel: it would have to be coarser than shows."""
    return t(
        "This look has more detail than fits in the room Libre Panel uses on this panel, and "
        "it is not written coarser: choose another look."
    )


def kilobytes(size: int) -> str:
    """A file's size as the app says it."""
    return f"{max(1, (size + 512) // 1024)} KB"


def no_room(name: str, other: int) -> str:
    """Why a file is not sent: with the other one that is on the panel it would be more than
    Libre Panel puts there (``other``: that one's bytes; it is one of before 0.9.4)."""
    if name == BOOT_LOGO:
        return t(
            "Not written: with the standby clip that is on the panel ({size}), the boot logo "
            "would be more than Libre Panel puts on this panel. Write the standby clip again "
            "first: it becomes smaller.",
            size=kilobytes(other),
        )
    return t(
        "Not written: with the boot logo that is on the panel ({size}), the standby clip "
        "would be more than Libre Panel puts on this panel. Write the boot logo again first: "
        "it becomes smaller.",
        size=kilobytes(other),
    )


def not_taken() -> str:
    """What is said of a file the panel did not take."""
    return t(
        "The panel did not take the file: it has what it had before, and nothing was changed. "
        "One panel did that when it had no room left."
    )


def cut_off(name: str, written: int, size: int) -> str:
    """What is said of a file that ended early and was taken off again."""
    if name == BOOT_LOGO:
        return t(
            "The boot logo did not fit: the panel took {written} of {size} bytes. It was taken "
            "off again, so that the panel is not left full; the factory logo shows at the next "
            "power-on.",
            written=written, size=size,
        )  # fmt: skip
    return t(
        "The standby clip did not fit: the panel took {written} of {size} bytes. It was taken "
        "off again, so that the panel is not left full: the panel has no standby clip now. "
        "Dark is the smallest look.",
        written=written, size=size,
    )  # fmt: skip


def still_there(name: str, written: int, size: int, why: str) -> str:
    """What is said of a file that ended early and could not be taken off again."""
    return t(
        "{file} did not fit (the panel took {written} of {size} bytes) and could not be taken "
        "off again: {why}",
        file=name, written=written, size=size, why=why,
    )  # fmt: skip


def models() -> list[PanelModel]:
    """The panels this works on: the TURZX USB ones."""
    return [model for model in MODELS if model.protocol == "usb-turing"]


def checked_name(name: str) -> bytes:
    """A file name as the panel takes it: relative, below ``usr/data/``, no ``..``."""
    if not isinstance(name, str) or not name.startswith(FOLDER + "/") or name.startswith("/"):
        raise DeviceError(f"{name!r}: only files below {FOLDER}/ are written")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts) or "\\" in name:
        raise DeviceError(f"{name!r}: not a plain file name")
    raw = name.encode("ascii", errors="strict") if name.isascii() else b""
    if not raw or len(raw) > 200 or not all(32 < byte < 127 for byte in raw):
        raise DeviceError(f"{name!r}: not a plain file name")
    return raw


def file_args(name: bytes, size: int = 0) -> bytes:
    return struct.pack(">I", len(name)) + struct.pack(">I", size) + name


# -- the pictures, as the panel takes them ------------------------------------------------------


def logo_jpeg(
    model: PanelModel, picture: Image.Image | None = None, orientation: str = "landscape",
    design: str = bootscreen.DEFAULT,
) -> bytes:  # fmt: skip
    """The boot logo for ``model`` (drawn upright for a bar on end with ``orientation``
    "portrait", in the look ``design``) as the JPEG the panel shows: turned into its
    framebuffer (480 x 1920 on the 9.2"), with every pixel's colour (a dark gradient's blocks
    showed at 88 with a colour for four), at the finest quality of :data:`JPEG_STEPS` that
    fits the logo's room on the model (:func:`room`). A picture that would have to be coarser
    than the last of them is not made."""
    native = to_native(picture or bootscreen.boot_logo(model, orientation, design)).convert("RGB")
    most = room(model)[1]
    for quality in JPEG_STEPS:
        buffer = io.BytesIO()
        native.save(buffer, format="JPEG", quality=quality, subsampling=0, optimize=True)
        if buffer.tell() <= most:
            return buffer.getvalue()
    raise DeviceError(too_fine())


def clip_command(ffmpeg: str, size: tuple[int, int], crf: float = CLIP_CRF,
                 full: bool = False) -> list[str]:  # fmt: skip
    """ffmpeg for the standby clip, as the panel's decoder plays it: one slice a picture, a
    sequence (headers and a keyframe) every 11 pictures, baseline, no B pictures; in full range
    for a panel whose decoder shows every stream so (``full``: the 9.2" and the 12.3" showed
    the clip of before under a grey veil), and with the dark parts kept as smooth as the light
    ones (aq-mode 3)."""
    from libre_panel.devices.h264 import FULL_RANGE, colour_tags, stamp

    width, height = size
    convert = f"{FULL_RANGE},format=yuv420p" if full else "format=yuv420p"
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-framerate", str(bootscreen.CLIP_FPS), "-i", "-", "-an", "-vf", convert,
        "-c:v", "libx264", "-profile:v", "baseline", "-preset", "slow", "-threads", "1",
        "-crf", f"{crf:g}", *colour_tags(full),
        "-x264-params",
        "sliced-threads=0:bframes=0:scenecut=0:keyint=11:min-keyint=11:repeat-headers=1"
        ":aq-mode=3",
        *stamp(full), "-f", "h264", "-",
    ]  # fmt: skip


def clip_h264(
    model: PanelModel,
    ffmpeg: str,
    frames: list[Image.Image] | None = None,
    orientation: str = "landscape",
    design: str = bootscreen.DEFAULT,
    full: bool = False,
) -> bytes:
    """The standby clip for ``model`` (upright with ``orientation`` "portrait", in the look
    ``design``, in full range with ``full``): its pictures turned into the framebuffer and
    encoded as finely as fits the clip's room on the model (:func:`room`): at crf
    :data:`CLIP_CRF`, coarser where that is too much, up to :data:`CLIP_CRF_MAX`, and then a
    quarter of a step at a time finer where that still fits (:data:`CLIP_QUARTERS`). A clip that
    would have to be coarser is not made."""
    from libre_panel.devices.h264 import slice_count

    pictures = frames or bootscreen.standby_frames(model, orientation=orientation, design=design)
    native = [to_native(frame).convert("RGB") for frame in pictures]
    raw = b"".join(frame.tobytes() for frame in native)

    def encoded(crf: float) -> bytes:
        try:
            done = subprocess.run(
                clip_command(ffmpeg, native[0].size, crf, full), input=raw,
                capture_output=True, timeout=300, check=False,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DeviceError(f"ffmpeg could not make the standby clip: {exc}") from exc
        if done.returncode != 0 or not done.stdout:
            raise DeviceError(f"ffmpeg could not make the standby clip: {done.stderr[-300:]!r}")
        return done.stdout

    most = room(model)[0]
    crf = CLIP_CRF
    data = encoded(crf)
    if len(data) > most:
        guess = len(data)
        while guess > most and crf < CLIP_CRF_MAX:  # (about 13 % less with each step)
            crf, guess = crf + 1, guess * CLIP_STEP // 1000
        data = encoded(crf)
        if len(data) > most:  # the guess was too fine: coarser, a step at a time
            while len(data) > most and crf < CLIP_CRF_MAX:
                crf += 1
                data = encoded(crf)
            if len(data) > most:
                raise DeviceError(too_fine())
        else:  # or too coarse: the finest that fits
            while crf - 1 > CLIP_CRF:
                finer = encoded(crf - 1)
                if len(finer) > most:
                    break
                crf, data = crf - 1, finer
        # (the whole step finer is too large) the finest quarter between the two that fits
        tried = [crf - quarter for quarter in CLIP_QUARTERS]
        with ThreadPoolExecutor(len(tried)) as pool:
            made = list(pool.map(encoded, tried))
        data = next((finer for finer in made if len(finer) <= most), data)
    if slice_count(data) != len(native):  # one slice a picture, or the panel shows nothing
        raise DeviceError("the standby clip came out with more than one slice a picture")
    return data


# -- on the panel -------------------------------------------------------------------------------


def unwritten(size: int, took: float) -> bool:
    """Whether a file of ``size`` bytes that the panel answered after ``took`` seconds cannot
    have been written: faster than any write is (:data:`WRITE_RATE_MOST`)."""
    return size >= WRITE_TIMED_FROM and took * WRITE_RATE_MOST < size


@dataclass
class PanelFiles:
    """The file commands on an open panel (``transport``: a synced UsbTransport; ``model``:
    the panel's, which says how much room its files get)."""

    transport: Any
    model: PanelModel | None = None

    def _command(self, cmd: int, args: bytes = b"", payload: bytes = b"",
                 timeout_ms: int = 4000) -> bytes:  # fmt: skip
        if cmd in NEVER:
            raise DeviceError(f"command {cmd} is never sent")
        return self.transport.command(cmd, args, payload, timeout_ms=timeout_ms)

    def listing(self, folder: str = FOLDER) -> list[str]:
        """The names in ``folder``, as the panel lists them (all 20 pieces are fetched)."""
        name = folder.encode("ascii")
        if folder != FOLDER:
            raise DeviceError(f"only {FOLDER} is listed")
        pieces = []
        for n in range(LIST_PIECES):
            # a piece of the listing's buffer: the 9.2" sends it as it is, without the
            # command's number in front (its first piece began with zeros); where that header
            # does come, the piece follows it
            reply = bytes(self.transport.ask(CMD_LIST, file_args(name), echo=False))
            if n == 0:
                log.debug("start screen: listing %s begins %s", folder, reply[:32].hex(" "))
            header = reply[:1] == bytes([CMD_LIST]) and reply[1:2] == bytes([ACK])
            pieces.append(reply[8 : 8 + PIECE] if header else reply[:PIECE])
        return names_in(b"".join(pieces))

    def noted_listing(self) -> list[str] | None:
        """The listing before a file is written, for the log; a panel that does not give it
        does not stop the writing (what was left unread is dropped)."""
        try:
            names = self.listing()
        except DeviceError as exc:
            log.warning("start screen: the panel's files could not be listed: %s", exc)
            self.transport.drain()
            return None
        log.info("start screen: %s holds %s", FOLDER, ", ".join(names) or "nothing listed")
        return names

    def size(self, name: str) -> int:
        """The size of a file of the panel's (0: none)."""
        reply = self.transport.ask(CMD_FILE_SIZE, file_args(checked_name(name)))
        size = struct.unpack("<I", bytes(reply[8:12]))[0]
        return 0 if size == 0xFFFFFFFF else size

    def write(self, name: str, data: bytes) -> None:
        """Write the boot logo or the standby clip, checked before and after (issue 52: a 9.2"
        had no room left for one, and 0.9.1 forced it in).

        Before: a file larger than its room on the model is not sent at all; then what the
        panel has of this file and of the other one. A file larger than the one it replaces
        is not sent where both together would be more than the room Libre Panel uses on the
        model. After: the size again. A file the panel did not take (it has
        what it had: a panel without room answers at once and changes nothing) is said so. A
        file that ended early is taken off again, so that the panel is not left full, and
        said so. Nothing is ever taken away in order to write it a second time."""
        if not writes_on(self.model):
            raise DeviceError(writes_off())
        raw = checked_name(name)
        if name not in OURS:
            raise DeviceError(f"{name}: only the boot logo and the standby clip are written")
        if not data or len(data) > MAX_FILE:
            raise DeviceError(f"{name}: a file is 1 byte to 1 MiB, not {len(data)}")
        most = room(self.model)[name == BOOT_LOGO]
        if len(data) > most:  # (the files made here never are: nothing else gets through)
            raise DeviceError(f"{name}: {len(data)} bytes are more than the {most} it gets")
        beside = STANDBY_CLIP if name == BOOT_LOGO else BOOT_LOGO
        before, other = self.size(name), self.size(beside)
        if len(data) > before and len(data) + other > sum(room(self.model)):
            log.warning("start screen: %s (%d bytes) not sent: %s on the panel has %d",
                        name, len(data), beside, other)  # fmt: skip
            raise DeviceError(no_room(name, other))
        began = time.monotonic()
        self._command(CMD_WRITE_FILE, file_args(raw, len(data)), data, timeout_ms=20000)
        took = time.monotonic() - began
        after = self.size(name)
        log.info(
            "start screen: %s: %d bytes sent, answered after %d ms; the panel had %d bytes of "
            "it and has %d",
            name, len(data), round(took * 1000), before, after,
        )  # fmt: skip
        if after == len(data) and not (before == after and unwritten(len(data), took)):
            return
        if after == before:
            raise DeviceError(not_taken())
        try:  # it ended early: off again, so that the panel is not left full
            self.delete(name)
            left = self.size(name)
        except DeviceError as exc:
            raise DeviceError(still_there(name, after, len(data), str(exc))) from exc
        if left:
            raise DeviceError(still_there(name, after, len(data), f"{left} bytes of it stay"))
        log.warning("start screen: %s ended at %d of %d bytes: taken off again", name, after,
                    len(data))  # fmt: skip
        raise DeviceError(cut_off(name, after, len(data)))

    def delete(self, name: str) -> None:
        raw = checked_name(name)
        if name not in OURS:
            raise DeviceError(f"{name}: only the boot logo and the standby clip are taken off")
        self._command(CMD_DELETE_FILE, file_args(raw))

    def show_jpeg(self, name: str) -> None:
        self._command(CMD_SHOW_JPEG, file_args(checked_name(name)))

    def play(self, name: str) -> None:
        """Play a clip of the panel's at the rate of a cold start; its name goes into the
        panel's settings (in memory, until :meth:`save_start`)."""
        raw = checked_name(name)  # never empty: an empty name, saved, loses the standby clip
        self._command(CMD_FRAME_RATE, bytes([bootscreen.CLIP_FPS]))
        self._command(CMD_PLAY_LOCAL, file_args(raw))

    def save_start(self, brightness: int) -> None:
        """Save the panel's settings: its brightness, to start with the clip :meth:`play`
        named, unturned (Libre Panel turns its pictures itself), and to dim when the PC has
        been gone for 10 s."""
        self._command(CMD_SAVE_SETTINGS, bytes([brightness_arg(brightness), START_WITH_CLIP,
                                                0, 0, 0, 0]))  # fmt: skip


def names_in(listing: bytes) -> list[str]:
    """The file names in a folder's listing as the panel gives it: the plain names in the
    text, in order, once each."""
    import re

    text = listing.replace(b"\x00", b"\n").decode("latin-1")
    found = []
    for token in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_.\-]*\.[A-Za-z0-9]+", text):
        if token not in found:
            found.append(token)
    return found


# -- what the app asks for ----------------------------------------------------------------------


Opener = Callable[..., tuple[Any, PanelModel]]
# Which panel: what the other panels name, and how many alike to pass over (UsbTransport.open)
Where = tuple[tuple[str, ...], int]
FIRST: Where = ((), 0)


def open_panel(
    model: PanelModel | None = None, beside: tuple[str, ...] = (), skip: int = 0
) -> tuple[UsbTransport, PanelModel]:
    """The TURZX USB panel on the bus (``model`` if given), opened and in step; ``beside`` and
    ``skip`` find a panel beside the first as its loop does."""
    pid = model.usb_ids[0][1] if model is not None and model.usb_ids else None
    transport = UsbTransport.open(pid, beside, skip)
    found = USB_PIDS.get(transport.pid) or model
    if found is None:
        transport.close()
        raise DeviceError(t("No TURZX USB panel found"))
    try:
        transport.sync()
    except DeviceError:
        transport.close()
        raise
    return transport, found


class StartScreen:
    """The jobs of the app's *Start screen*: what is on the panel, the boot logo written or
    taken away, the standby clip written and saved. Each opens the panel and closes it again;
    the app pauses its themes first. ``opener`` and ``ffmpeg`` are for the tests."""

    def __init__(self, opener: Opener = open_panel, ffmpeg: Callable[[], str] | None = None):
        self.opener = opener
        self.ffmpeg = ffmpeg

    def _with_panel(self, model: PanelModel | None, job: Callable[[PanelFiles, PanelModel], Any],
                    where: Where = FIRST):  # fmt: skip
        transport, found = self.opener(model) if where == FIRST else self.opener(model, *where)
        try:
            return job(PanelFiles(transport, found), found)
        finally:
            transport.close()

    def state(self, model: PanelModel | None = None, where: Where = FIRST) -> dict[str, Any]:
        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            clip, logo = room(found)
            return {
                "model": found.id,
                "files": files.noted_listing(),  # (None: the panel did not list them)
                "boot_logo": files.size(BOOT_LOGO),
                "standby_clip": files.size(STANDBY_CLIP),
                # (what Libre Panel writes at most: files of before 0.9.4 may be larger)
                "room": {"clip": clip, "logo": logo},
            }

        return self._with_panel(model, job, where)

    def write_logo(
        self, model: PanelModel | None = None, orientation: str = "landscape",
        where: Where = FIRST, design: str = bootscreen.DEFAULT,
    ) -> dict[str, Any]:  # fmt: skip
        """The boot logo written and shown (it shows at the next cold start)."""
        if model is not None and not writes_on(model):
            raise DeviceError(writes_off())  # (said before the panel is opened)

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            if not writes_on(found):
                raise DeviceError(writes_off())
            data = logo_jpeg(found, orientation=orientation, design=design)
            files.noted_listing()  # what is there before, in the log of the panel's folder
            files.write(BOOT_LOGO, data)
            files.show_jpeg(BOOT_LOGO)
            return {"model": found.id, "boot_logo": len(data), "design": design}

        return self._with_panel(model, job, where)

    def remove_logo(self, model: PanelModel | None = None, where: Where = FIRST) -> dict[str, Any]:
        """The boot logo taken away: the factory logo shows again at the next cold start."""

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            files.delete(BOOT_LOGO)
            return {"model": found.id, "boot_logo": files.size(BOOT_LOGO)}

        return self._with_panel(model, job, where)

    def write_clip(
        self, brightness: int, model: PanelModel | None = None, orientation: str = "landscape",
        where: Where = FIRST, design: str = bootscreen.DEFAULT, video_range: str = "auto",
    ) -> dict[str, Any]:  # fmt: skip
        """The standby clip written, played (as it will be) and saved as what the panel
        starts with; in the range of values its decoder shows ([device] video_range, "auto"
        as its model is known to)."""
        from libre_panel.devices.h264 import find_ffmpeg
        from libre_panel.devices.turzx_video import full_range

        if model is not None and not writes_on(model):
            raise DeviceError(writes_off())  # (said before the panel is opened)
        ffmpeg = self.ffmpeg() if self.ffmpeg else find_ffmpeg()

        def job(files: PanelFiles, found: PanelModel) -> dict[str, Any]:
            if not writes_on(found):
                raise DeviceError(writes_off())
            data = clip_h264(found, ffmpeg, orientation=orientation, design=design,
                             full=full_range(video_range, found))  # fmt: skip
            files.noted_listing()
            files.write(STANDBY_CLIP, data)
            files.play(STANDBY_CLIP)
            files.save_start(brightness)
            return {"model": found.id, "standby_clip": len(data), "name": STANDBY_CLIP,
                    "design": design}  # fmt: skip

        return self._with_panel(model, job, where)


def _preview_scale(model: PanelModel, width: int, orientation: str) -> float:
    """How much smaller the app's picture is drawn than the panel's (at most as large)."""
    return min(1.0, width / bootscreen.design_size(model, orientation)[0])


def preview_png(model: PanelModel, width: int = 960, orientation: str = "landscape",
                design: str = bootscreen.DEFAULT) -> bytes:  # fmt: skip
    """The boot logo as a PNG for the app, ``width`` px wide (upright for a bar on end), drawn
    at that size."""
    scale = _preview_scale(model, width, orientation)
    picture = bootscreen.boot_logo(model, orientation, design, scale=scale)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def preview_clip(model: PanelModel, width: int = 960, orientation: str = "landscape",
                 design: str = bootscreen.DEFAULT) -> bytes:  # fmt: skip
    """The standby clip as an animated WebP for the app (an animated PNG where Pillow has no
    WebP), ``width`` px wide, at its rate, drawn at that size: a clip whose light moves all
    over (Aurora's) came to 14 MB as a PNG, and to a few hundred KB so."""
    from PIL import features

    scale = _preview_scale(model, width, orientation)
    frames = bootscreen.standby_frames(model, orientation=orientation, design=design, scale=scale)
    buffer = io.BytesIO()
    timing = {"save_all": True, "append_images": frames[1:], "loop": 0,
              "duration": round(1000 / bootscreen.CLIP_FPS)}  # fmt: skip
    if features.check("webp"):
        frames[0].save(buffer, format="WEBP", quality=90, method=4, **timing)
    else:
        frames[0].save(buffer, format="PNG", **timing)
    return buffer.getvalue()


def picture_type(data: bytes) -> str:
    """The media type of a picture of :func:`cached_preview`."""
    return "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "image/png"


def model_of(model_id: str) -> PanelModel:
    """A TURZX USB panel by its id."""
    found = next((model for model in models() if model.id == model_id), None)
    if found is None:
        raise DeviceError(f"{model_id!r} is no TURZX USB panel")
    return found


def design_of(name: str | None) -> str:
    """A look of the panel's own screens by its name (render/bootscreen.py), Libre Panel's own
    for none; DeviceError for one there is not."""
    try:
        return bootscreen.design_of(name).id
    except KeyError:
        raise DeviceError(f"{name!r} is no look of the start screen") from None


@lru_cache(maxsize=16)
def cached_preview(model_id: str, what: str, width: int, orientation: str = "landscape",
                   design: str = bootscreen.DEFAULT) -> bytes:  # fmt: skip
    """The app's picture of the boot logo (PNG) or of the standby clip (animated)."""
    model = model_of(model_id)
    if what == "clip":
        return preview_clip(model, width, orientation, design)
    return preview_png(model, width, orientation, design)


@lru_cache(maxsize=4)
def cached_file(model_id: str, what: str, orientation: str = "landscape",
                design: str = bootscreen.DEFAULT, full: bool = False) -> bytes:  # fmt: skip
    """The boot logo's JPEG or the standby clip as the panel takes them (in full range with
    ``full``), to keep or to put on the panel with another program."""
    from libre_panel.devices.h264 import find_ffmpeg

    model = model_of(model_id)
    if what == "clip":
        return clip_h264(model, find_ffmpeg(), orientation=orientation, design=design, full=full)
    return logo_jpeg(model, orientation=orientation, design=design)
