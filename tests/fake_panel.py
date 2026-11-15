"""A simulated Turing V1.x USB panel for tests.

It checks every packet the way the real device needs it (DES, magic, trailer,
size field, RGBA PNG, a JPEG as wide as the decoder's layer) and answers with
echo + ACK, including the 512-byte replies whose zero-length packet trips up
readers that ask for exactly 512.

The video path is simulated too: 110 must carry a name, 121 blocks go into a
ring of five (overflow silently drops the oldest, as on the panel), and a
player takes one block per frame at the rate set with 15. 122 answers with
the queue depth, optionally late.
"""

from __future__ import annotations

import io
import struct
import time
from collections.abc import Callable, Iterator

from PIL import Image

from libre_panel.devices.turzx_usb import ACK, READ_LEN, UsbTransport, decode_packet


class Unplugged(Exception):
    """Stands in for usb.core.USBError."""


STANDBY_CLIP = "usr/data/standby.h264"  # (the panel's standby clip)


class FakePanel:
    RING = 5
    MAX_BLOCK = 202752

    def __init__(
        self, pid: int = 0x0092, native=(480, 1920), clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.pid = pid
        self.native = native
        self.clock = clock
        self.commands: list[int] = []
        self.frames: list[Image.Image] = []  # the PNG layer (102)
        self.jpegs: list[Image.Image] = []  # the layer under it (101)
        # The JPEG decoder's layer: on the 9.2" 464 columns (29 blocks of 16, 462 of them show),
        # not the framebuffer's 480; a JPEG of 480 wraps its last rows onto the first.
        self.jpeg_width = 464 if pid == 0x0092 else native[0]
        self.brightness: list[int] = []
        self.pending: list[bytes] = []
        self.unplugged = False
        self.fail_next_writes = 0
        # video
        self.fps = 30  # the player's rate after power-on
        self.fps_log: list[int] = []
        self.clip_names: list[str] = []
        self.video_ready = False  # 110 cleared the framebuffer; without it the screen stays black
        self.blocks: list[bytes] = []  # every 121 payload, in order
        self.queue: list[tuple[float, bytes]] = []
        self.played = 0
        self.overwritten = 0
        self.busy_until = 0.0
        self.late_status = 0  # the next n 122 answers come late (after the next command)
        self._late: list[bytes] = []
        self.hung_depths: Iterator[int] | None = None  # a hung decoder: fixed answers to 122
        self.block_delay = 0.0  # a slow panel: seconds each 121 block takes
        # 11 restarts the panel: off the bus for ``restart_away_s``, then back as after power-on
        self.restart_away_s = 0.3
        self.restarts = 0
        self.away_until = 0.0
        self.recovers = True  # False: the restart does not clear the hung decoder
        # the panel's files (40 written, 42 deleted, 98 their size, 99 the folder's listing in
        # 20 pieces of 512 without a header, as the 9.2" sends it), 113 shown, 125 saved
        self.files: dict[str, bytes] = {}
        # the bytes the panel's folder holds (None: no end): a file is written anew only where
        # it fits beside the one it replaces, else the panel keeps what it had, as a 9.2" kept
        # its standby clip when a larger one came
        self.room: int | None = None
        self.shown: list[str] = []
        self.saved: list[bytes] = []
        self._piece = 0
        # what the panel names itself in its answer to the sync
        self.hello = b"turzx_00"
        # a panel whose program is not running: it is on the bus and takes no packet
        self.deaf = False
        # files a 42 does not take off (it is accepted all the same), and commands that are
        # taken and never answered
        self.kept_files: set[str] = set()
        self.unanswered: set[int] = set()
        # commands after which the panel leaves the bus at once, without an answer, as one
        # whose program ends there (each once), and whether a read fails then, as a panel
        # that is gone fails it (else it times out, as one that restarts does)
        self.leaves_once: set[int] = set()
        self.gone_reads_fail = False
        # a panel that plays its standby clip, as one set to start with it does: it does not
        # let the clip's file go while it plays it (42 is accepted, the file stays), and 111
        # stops the playing. With ``clip_s`` it restarts at the clip's end for as long as it
        # plays: on the bus for that long, off it for ``restart_away_s``, and playing again
        # when it is back
        self.plays_clip = False
        self.clip_s: float | None = None
        self._plays_since: float | None = None
        # a panel whose program does not read what is sent: its endpoint takes that many
        # packets each time it is on the bus and no more (the next one waits until the time
        # given is over, and fails when the panel leaves), and none is answered. None: it
        # reads
        self.takes: int | None = None
        self._taken = 0
        # a panel whose program is up only ``wakes_s`` after it came: what it is sent before
        # that waits in its endpoint and is answered then, in order
        self.wakes_s: float | None = None
        self._since: float | None = None
        self._dozing: list[bytes] = []

    @property
    def present(self) -> bool:
        """On the bus (not unplugged, not in the middle of a restart)."""
        self._clip_end()
        there = not self.unplugged and time.monotonic() >= self.away_until
        if not there:  # (it comes back as after power-on: its endpoint empty, its program down)
            self._taken, self._since, self._dozing = 0, None, []
        elif self._since is None:
            self._since = time.monotonic()
        return there

    def _dozes(self) -> bool:
        """Its program is not up yet (``wakes_s`` after it came)."""
        if self.wakes_s is None or self._since is None:
            return False
        return time.monotonic() - self._since < self.wakes_s

    def _clip_end(self) -> None:
        """The restart of a panel that fails at its clip's end (``clip_s``)."""
        now = time.monotonic()
        if self.clip_s is None or self.unplugged:
            self._plays_since = None
            return
        if now < self.away_until:
            return
        if self._plays_since is None:  # it is back: it plays its clip from the start
            self._plays_since = now
            self.plays_clip = STANDBY_CLIP in self.files
        elif self.plays_clip and now - self._plays_since >= self.clip_s:
            self.away_until = now + self.restart_away_s
            self._plays_since, self.pending = None, []

    def _restart(self) -> None:
        self.restarts += 1
        self.away_until = time.monotonic() + self.restart_away_s
        self.fps, self.video_ready, self.queue, self.pending = 30, False, [], []
        if self.recovers:
            self.hung_depths = None

    def _play(self) -> None:
        """The panel's player: one block per frame, taken when the previous frame is done."""
        if self.hung_depths is not None:
            return
        now, frame = self.clock(), 1 / self.fps
        while self.queue:
            start = max(self.busy_until, self.queue[0][0])
            if start > now:
                break
            self.queue.pop(0)
            self.played += 1
            self.busy_until = start + frame

    def stream(self) -> bytes:
        """Everything received on the video path."""
        return b"".join(self.blocks)

    # endpoint OUT
    def write(self, data: bytes, timeout: int) -> None:
        import usb.core

        if not self.present or self.fail_next_writes:
            self.fail_next_writes = max(0, self.fail_next_writes - 1)
            raise usb.core.USBError("No such device (it may have been disconnected)", errno=19)
        if self.deaf:
            time.sleep(min(timeout, 20) / 1000)
            raise usb.core.USBTimeoutError("Operation timed out")
        data = bytes(data)
        if self.takes is not None:  # (nothing reads: its endpoint takes so many, the rest wait)
            if self._taken >= self.takes:
                until = time.monotonic() + timeout / 1000
                while time.monotonic() < until:
                    if not self.present:
                        gone = "No such device (it may have been disconnected)"
                        raise usb.core.USBError(gone, errno=19)
                    time.sleep(0.005)
                raise usb.core.USBTimeoutError("Operation timed out")
            self._taken += 1
            self.commands.append(decode_packet(data[:512])[0])
            return
        plain = decode_packet(data[:512])  # checks trailer and magic
        cmd = plain[0]
        self.commands.append(cmd)
        payload = data[512:]
        self.pending.extend(self._late)  # a late answer arrives before the next one
        self._late.clear()
        if cmd in (101, 102, 121):
            size = struct.unpack(">I", plain[8:12])[0]
            assert size == len(payload), f"size field {size} != payload {len(payload)}"
        if cmd == 102:
            assert payload[25] == 6, "panel needs RGBA PNGs (colour type 6)"
            with Image.open(io.BytesIO(payload)) as png:
                assert png.size == self.native, f"frame {png.size} != framebuffer {self.native}"
                self.frames.append(png.copy())
        if cmd == 101:
            assert len(payload) <= 1024 * 1024, "payload over 1 MiB: the panel times out"
            with Image.open(io.BytesIO(payload)) as jpeg:
                assert jpeg.format == "JPEG" and jpeg.mode == "RGB", (jpeg.format, jpeg.mode)
                assert not jpeg.info.get("progressive"), "the decoder takes baseline JPEGs"
                wanted = (self.jpeg_width, self.native[1])
                assert jpeg.size == wanted, f"JPEG {jpeg.size} != the decoder's layer {wanted}"
                self.jpegs.append(jpeg.copy())
        if cmd in (40, 42, 98, 99, 113):
            length, size = struct.unpack(">II", plain[8:16])
            name = bytes(plain[16 : 16 + length]).decode("ascii")
            assert name.startswith("usr/data"), name
        if cmd == 40:
            assert size == len(payload) and 0 < size <= 1024 * 1024, (size, len(payload))
            used = sum(len(data) for data in self.files.values())
            if self.room is None or used + size <= self.room:
                self.files[name] = payload
        playing = self.plays_clip and cmd == 42 and name == STANDBY_CLIP
        if cmd == 42 and name not in self.kept_files and not playing:
            self.files.pop(name, None)
        if cmd == 111:
            self.plays_clip = False
        if cmd == 113:
            assert name in self.files, f"{name} shown but not there"
            self.shown.append(name)
        if cmd == 125:
            self.saved.append(bytes(plain[8:14]))
        if cmd == 99:
            listing = b"\0".join(n.rsplit("/", 1)[1].encode() for n in self.files)
            start = self._piece * 512
            self._piece = (self._piece + 1) % 20
            self.pending.append(listing[start : start + 512].ljust(512, b"\0"))
            return
        if cmd == 14:
            assert 0 <= plain[8] <= 102
            self.brightness.append(plain[8])
        reply = bytearray(bytes([cmd, ACK]) + (self.hello if cmd == 10 else b"")).ljust(512, b"\0")
        if cmd == 110:
            size = struct.unpack(">I", plain[8:12])[0]
            name = bytes(plain[16 : 16 + size])
            assert name, "an empty name erases the panel's standby clip setting"
            self.clip_names.append(name.decode("utf-8"))
            self.video_ready = True
        if cmd == 15:
            assert 1 <= plain[8] <= 120
            self.fps = plain[8]
            self.fps_log.append(plain[8])
        if cmd == 121:
            assert len(payload) <= self.MAX_BLOCK, "block larger than the panel's buffer"
            assert self.video_ready, "121 before 110: the screen stays black"
            if self.block_delay:
                time.sleep(self.block_delay)
            self._play()
            if len(self.queue) >= self.RING:  # the ring overwrites an unread block
                self.queue.pop(0)
                self.overwritten += 1
            self.queue.append((self.clock(), payload))
            self.blocks.append(payload)
        if cmd == 98:
            found = self.files.get(name)
            reply[8:12] = struct.pack("<I", 0xFFFFFFFF if found is None else len(found))
        if cmd == 122:
            self._play()
            depth = next(self.hung_depths) if self.hung_depths else len(self.queue)
            reply[8] = depth
            if self.late_status:
                self.late_status -= 1
                self._late.append(bytes(reply))
                return
        if cmd == 123:
            self.queue.clear()
            self.video_ready = False
        if cmd == 11:
            self._restart()  # gone before it answers
            return
        if cmd in self.leaves_once:
            self.leaves_once.discard(cmd)
            self.away_until = time.monotonic() + self.restart_away_s
            self.pending = []
            return
        if cmd in self.unanswered:
            return
        if self._dozes():
            self._dozing.append(bytes(reply))
            return
        self.pending.extend(self._dozing)  # (up: what it was sent meanwhile is answered first)
        self._dozing.clear()
        self.pending.append(bytes(reply))

    # endpoint IN
    def read(self, length: int, timeout: int) -> bytes:
        import usb.core

        assert length >= READ_LEN, "read at least 1024 bytes or the ZLP stays in the pipe"
        if not self.present and self.gone_reads_fail:
            raise usb.core.USBError("No such device (it may have been disconnected)", errno=19)
        if self._dozing and not self._dozes():
            self.pending.extend(self._dozing)
            self._dozing.clear()
        if not self.present or not self.pending:
            raise usb.core.USBTimeoutError("Operation timed out")
        return self.pending.pop(0)


def transport_for(panel: FakePanel) -> UsbTransport:
    return UsbTransport(device=None, ep_out=panel, ep_in=panel, pid=panel.pid)
