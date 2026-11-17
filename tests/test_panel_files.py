"""The panel's own screens on a TURZX USB panel: the boot logo and the standby clip in each of
their looks for every USB panel's size, as finely as fits the room they get on a panel, and
what is sent to put them on it (a fake panel keeps the files and every command)."""

import io
import struct
import time
from types import SimpleNamespace

import numpy
import pytest
from PIL import Image
from test_editor_server import request, server  # noqa: F401 (a fixture)

from libre_panel.devices import panel_files as F
from libre_panel.devices.base import DeviceError
from libre_panel.devices.h264 import find_ffmpeg, nal_units, slice_count
from libre_panel.devices.models import get_model
from libre_panel.render import bootscreen

NINE = get_model("turing-9.2-usb")
TWELVE = get_model("turing-12.3-usb")
EIGHT = get_model("turing-8.8-usb")
WRITES_OFF = (
    "No one has tried start screens on this panel yet: they are written to it only where you "
    "agree to try it, at your own risk, in Settings → Panel → Start screen."
)
TOO_FINE = (
    "This look has more detail than fits in the room Libre Panel uses on this panel, and it "
    "is not written coarser: choose another look."
)
NOT_TAKEN = (
    "The panel did not take the file: it has what it had before, and nothing was changed. "
    "One panel did that when it had no room left."
)


# (files are written to the 9.2" and the 12.3" alone; the pictures and what is sent are tried
# on the family's other sizes too, as who looks into one of those switches it on)
pytestmark = pytest.mark.usefixtures("panel_writes")


class FakePanel:
    """A TURZX USB panel's files and settings, and the commands it was sent."""

    def __init__(self, files=None, listing="header", room=None, refuses=False, rate=None):
        self.files = dict(files or {})
        self.listing = listing  # "header": [99, ack] in front; "bare": the piece alone (the 9.2")
        # the bytes its folder holds (None: no end). A file that does not fit there beside the
        # others ends where the room does (a 9.2"'s boot logo ended at 16 KiB, its clip at the
        # size of the one it replaced: a file's own room is free for the one in its place)
        self.room = room
        # a panel that takes no file any more, as that 9.2" did once it was full: it answers
        # at once and keeps what it had
        self.refuses = refuses
        self.rate = rate  # the bytes a second it writes (None: a write takes no time)
        self.refusal = 0.0  # the seconds its answer takes where it refuses
        self.wait = time.sleep  # how that time passes (a test with its own clock: its own)
        self.sent = []  # (command, name or args, payload size)
        self.listed = 0
        self.closed = False

    @staticmethod
    def name_of(args):
        size = struct.unpack(">I", args[:4])[0]
        return args[8 : 8 + size].decode("ascii")

    def command(self, cmd, args=b"", payload=b"", timeout_ms=2000):
        assert cmd not in (11, 12, 13), cmd
        if cmd in (40, 42, 110, 113):
            name = self.name_of(args)
            assert name, "a file command with an empty name"
            self.sent.append((cmd, name, len(payload)))
            if cmd == 40 and self.refuses and self.refusal:
                self.wait(self.refusal)
            if cmd == 40 and not self.refuses:
                assert struct.unpack(">I", args[4:8])[0] == len(payload)
                others = sum(len(data) for other, data in self.files.items() if other != name)
                fits = len(payload)
                if self.room is not None:
                    fits = max(0, min(fits, self.room - others))
                if self.rate:
                    self.wait(fits / self.rate)
                if fits or name in self.files:
                    self.files[name] = payload[:fits]
            if cmd == 42:
                self.files.pop(name, None)
        else:
            self.sent.append((cmd, bytes(args), len(payload)))
        return bytes([cmd, 0xC8]) + bytes(510)

    def ask(self, cmd, args=b"", timeout_ms=2000, echo=True):
        name = self.name_of(args)
        if cmd == 98:
            self.sent.append((98, name, 0))
            return bytes([98, 0xC8]) + bytes(6) + struct.pack("<I", len(self.files.get(name, b"")))
        assert cmd == 99 and name == "usr/data"
        self.sent.append((99, name, 0))
        text = "".join(f"{n.rsplit('/', 1)[1]}\0" for n in sorted(self.files)).encode()
        piece = text.ljust(F.LIST_PIECES * F.PIECE, b"\0")[
            self.listed * F.PIECE : (self.listed + 1) * F.PIECE
        ]
        self.listed = (self.listed + 1) % F.LIST_PIECES
        if self.listing == "bare":
            assert not echo  # (a check for the command's number would refuse it)
            return piece
        if self.listing == "none":
            return bytes(16)  # a panel that does not list (the reply of the error)
        return bytes([99, 0xC8]) + bytes(6) + piece

    def drain(self):
        self.drained = True

    def close(self):
        self.closed = True


def jobs(panel, model=NINE):
    return F.StartScreen(opener=lambda asked: (panel, asked or model), ffmpeg=find_ffmpeg)


# -- the pictures ------------------------------------------------------------------------------


@pytest.mark.parametrize("model", F.models(), ids=lambda m: m.id)
def test_every_usb_panel_gets_its_boot_logo_and_clip_in_its_size(model):
    logo = bootscreen.boot_logo(model)
    assert logo.size == model.size("landscape") and logo.mode == "RGB"
    pixels = numpy.asarray(logo, float)
    long_side = max(logo.size)
    quiet = round(bootscreen.QUIET * long_side)
    ends = numpy.concatenate([pixels[:, :quiet], pixels[:, -quiet:]], axis=1)
    assert ends.max() < 24  # the short ends are dark: the boot JPEG leaves them as they were
    middle = pixels[:, quiet:-quiet]
    assert middle.max() > 200  # the name and the mark are lit
    lay = bootscreen.layout(model)
    left, top, right, bottom = model.hidden
    assert lay.mark[1] >= top and lay.line[2] < logo.size[1] - bottom  # out of the hidden strip
    frames = bootscreen.standby_frames(model, 8)
    assert len({frame.tobytes() for frame in frames}) == 8  # it moves ...
    first, other = (numpy.asarray(f, float) for f in frames[:2])
    moved = numpy.argwhere(numpy.abs(first - other).max(axis=2) > 3)
    assert moved[:, 0].min() >= lay.mark[1] - lay.mark[2]  # ... where the mark and the line are


def test_a_bar_on_end_gets_them_upright():
    logo = bootscreen.boot_logo(NINE, "portrait")
    assert logo.size == (480, 1920)
    pixels = numpy.asarray(logo, float)
    quiet = round(bootscreen.QUIET * 1920)
    ends = numpy.concatenate([pixels[:quiet], pixels[-quiet:]], axis=0)
    assert ends.max() < 24  # the short ends, now at the top and the bottom, stay dark
    lay = bootscreen.layout(NINE, "portrait")
    assert min(lay.mark[0], lay.name[0], lay.line[0]) >= NINE.hidden_edges("portrait")["left"]
    jpeg = Image.open(io.BytesIO(F.logo_jpeg(NINE, orientation="portrait")))
    turned = numpy.asarray(logo.rotate(180).convert("L"), float)  # as a bar on end's frames
    assert numpy.abs(numpy.asarray(jpeg.convert("L"), float) - turned).mean() < 3
    frames = bootscreen.standby_frames(NINE, 4, orientation="portrait")
    assert {frame.size for frame in frames} == {(480, 1920)}


LOOKS = list(bootscreen.designs())
SMALL = [get_model("turing-2.1-usb"), get_model("turing-4.6-usb")]  # round, a bar: quick


@pytest.mark.parametrize("model", SMALL, ids=lambda m: m.id)
@pytest.mark.parametrize("design", LOOKS)
def test_every_look_is_drawn_for_every_panel_and_its_clip_loops(design, model):
    look = bootscreen.design_of(design)
    assert look.frames % bootscreen.SEQUENCE == 0  # whole sequences for the panel's decoder
    logo = bootscreen.boot_logo(model, design=design)
    assert logo.size == model.size("landscape") and logo.mode == "RGB"
    pixels = numpy.asarray(logo, float)
    assert pixels.max() > 40  # something is lit ...
    w, h = logo.size
    if model.shape == "round":  # ... and the corners the glass leaves out are dark
        assert pixels[:4, :4].max() < 8 and pixels[-4:, -4:].max() < 8
    else:  # ... and the short ends fade out
        edge = round(bootscreen.QUIET * w) // 4
        assert pixels[:, :edge].max() < 24 and pixels[:, -edge:].max() < 24
    frames = bootscreen.standby_frames(model, 4, design=design)
    assert len({frame.tobytes() for frame in frames}) >= 3  # it moves
    if design != bootscreen.DEFAULT:  # (Libre Panel's own light runs out anew each loop)
        drawn, _where = bootscreen._look(model, "landscape", design)
        seam = numpy.abs(drawn.frame(0.0) - drawn.frame(1.0)).max()
        assert seam < 0.5  # the loop ends where it begins


@pytest.mark.parametrize("model", ["turing-4.6-usb", "turing-9.2-usb", "turing-12.3-usb"])
def test_a_bar_on_end_gets_every_look_upright(model):
    model = get_model(model)
    for design in LOOKS:
        logo = bootscreen.boot_logo(model, "portrait", design, scale=0.25)
        w, h = logo.size
        room = bootscreen.room(model, "portrait", 0.25)
        assert h > w and (w, h) == (room.w, room.h), design
        pixels = numpy.asarray(logo, float)
        assert pixels.max() > 40, design  # something is lit
        edge = round(bootscreen.QUIET * h) // 4  # the short ends, top and bottom, fade out
        assert pixels[:edge].max() < 24 and pixels[-edge:].max() < 24, design


def test_a_heartbeat_runs_across_on_a_bar_on_end_too():
    """Pulse on a bar on end: its traces run across, one under the other, as on a monitor,
    not one trace on its side; a round panel has one."""
    tall = bootscreen._look(get_model("turing-8.8-usb"), "portrait", "pulse")[0]
    lit = numpy.argwhere(tall.cover > 0.5)
    rows = numpy.unique(numpy.round(lit[:, 0] / 40))  # (bands of 40 px the traces are lit in)
    across = numpy.ptp(lit[:, 1])
    assert across > 0.9 * tall.where.w and len(rows) >= 8
    lone = bootscreen._look(get_model("turing-2.1-usb"), "landscape", "pulse")[0]
    middle = numpy.argwhere(lone.cover[:, lone.where.w // 2 - 40 : lone.where.w // 2 + 40] > 0.5)
    assert numpy.ptp(middle[:, 0]) < lone.where.h * 0.2  # one trace through the middle


def test_an_unknown_look_is_refused():
    with pytest.raises(DeviceError, match="no look"):
        F.design_of("sunset")
    assert F.design_of("") == F.design_of(None) == bootscreen.DEFAULT == LOOKS[0]


def test_a_dark_gradient_has_no_bands_and_black_stays_black():
    """A gradient of a few levels across many rows, made 8 bits: dithered, each row comes to
    its level on average (rounded, it would stand still for a band and jump); black is 0."""
    h, w = 240, 960
    where = bootscreen.Room(w, h, 0, 0, w, h)
    ideal = numpy.linspace(6.0, 10.0, h, dtype=numpy.float32)
    canvas = numpy.repeat(ideal[:, None, None], w, axis=1).repeat(3, axis=2)
    canvas[:, 400:420] = 0  # a black stripe
    picture = numpy.asarray(bootscreen.finish(canvas, where), float)
    rows = picture[:, 100:380, 1].mean(axis=1)  # (away from the short ends, which fade)
    assert numpy.abs(rows - ideal).max() < 0.3
    rounded = numpy.floor(ideal + 0.5)
    assert numpy.abs(rounded - ideal).max() > 0.45  # (what rounding alone would have done)
    assert picture[:, 400:420].max() == 0


def test_a_glow_is_blurred_in_floating_point():
    light = numpy.zeros((60, 60), numpy.float32)
    light[30, 30] = 1000.0
    out = bootscreen.blur(light, 4.0)
    assert abs(out.sum() - 1000.0) < 1.0  # no light lost
    profile = out[30, 30:50]
    assert numpy.all(numpy.diff(profile) <= 0) and profile[9] > 0  # a smooth tail, not steps
    assert abs(out[30, 34] / out[30, 30] - numpy.exp(-0.5)) < 0.08  # as a Gaussian of 4 px


def test_the_light_runs_out_from_the_middle():
    count = 31
    middle = [bootscreen._wave(15, count, p / 44) for p in range(44)]
    end = [bootscreen._wave(0, count, p / 44) for p in range(44)]
    assert middle.index(max(middle)) < end.index(max(end))  # the middle is lit first
    assert min(middle + end) >= 0.28  # the light never goes out


def test_the_logo_is_a_jpeg_in_the_panels_framebuffer():
    from PIL import JpegImagePlugin

    data = F.logo_jpeg(NINE)
    picture = Image.open(io.BytesIO(data))
    assert picture.format == "JPEG" and picture.size == (480, 1920)  # turned as the frames are
    assert JpegImagePlugin.get_sampling(picture) == 0  # every pixel its colour
    assert len(data) <= F.room(NINE)[1]
    drawn = numpy.asarray(bootscreen.boot_logo(NINE).rotate(-90, expand=True), float)
    assert numpy.abs(numpy.asarray(picture.convert("RGB"), float) - drawn).mean() < 1.5


def _jpeg(picture, quality):
    buffer = io.BytesIO()
    F.to_native(picture).convert("RGB").save(buffer, format="JPEG", quality=quality,
                                             subsampling=0, optimize=True)  # fmt: skip
    return buffer.getvalue()


def test_the_logo_is_as_fine_as_fits_its_room():
    """The boot logo at the finest whole quality whose file fits the logo's room on the panel
    (120,000 bytes on the 9.2", 150,000 on the 12.3"), every pixel with its own colour; a look
    that would need a quality that shows (Levels, with its thousands of small lights: 491 KB
    at 95 on the 9.2") is not made coarser, and not made. (0.9.4 had steps of 95, 92, 90, 88:
    the default look's logo was 92 on the 9.2", Circuit's 90 on the 12.3".)"""
    assert F.JPEG_STEPS == (95, 94, 93, 92, 91, 90, 89, 88, 87, 86, 85)
    for model, design, quality in ((NINE, "aurora", 95), (NINE, "libre-panel", 94),
                                   (TWELVE, "circuit", 91)):  # fmt: skip
        picture = bootscreen.boot_logo(model, design=design)
        data = F.logo_jpeg(model, design=design)
        assert data == _jpeg(picture, quality), (model.id, design)
        assert len(data) <= F.room(model)[1], (model.id, design)
        if quality < 95:  # (the next finer step is too large: that is why it is this one)
            finer = F.JPEG_STEPS[F.JPEG_STEPS.index(quality) - 1]
            assert len(_jpeg(picture, finer)) > F.room(model)[1], (model.id, design)
    for model in (NINE, TWELVE, EIGHT):
        with pytest.raises(DeviceError) as refused:
            F.logo_jpeg(model, design="levels")
        assert str(refused.value) == TOO_FINE
        coarsest = _jpeg(bootscreen.boot_logo(model, design="levels"), F.JPEG_STEPS[-1])
        assert len(coarsest) > 2 * F.room(model)[1]  # (not by a little)
        # what the card is told beforehand is what the logos say: every other look fits
        fits = []
        for design in LOOKS:
            try:
                fits.append(design) if F.logo_jpeg(model, design=design) else None
            except DeviceError:
                pass
        assert set(LOOKS) - set(fits) == set(F.UNFIT[model.id]) == {"levels"}, model.id
    # a model no one has tried: its looks were measured for its room (the 9.2"'s by the
    # pixels), both files and both ways mounted, and one is not offered where either would be
    # too fine. The default look fits every one; the logos say their part here (the clips take
    # minutes, and were measured once, as for the models above)
    assert set(F.UNFIT) == {model.id for model in F.models()}
    for model in F.models():
        if F.tried(model):
            continue
        assert "levels" in F.UNFIT[model.id] and bootscreen.DEFAULT not in F.UNFIT[model.id]
        assert set(F.UNFIT[model.id]) <= set(LOOKS), model.id
        for design in LOOKS:
            try:
                F.logo_jpeg(model, design=design)
            except DeviceError:
                assert design in F.UNFIT[model.id], (model.id, design)


def test_the_room_a_panels_files_get():
    """How much Libre Panel puts on a panel (issue 52): on the 9.2" two thirds of the 718,000
    bytes that filled one, on the 12.3" four fifths of the 817,587 one has held for weeks; on
    a model it has not written to (for who switches that on), the 9.2"'s for as many pixels
    as it has, and never more."""
    assert F.room(NINE) == (360_000, 120_000) and F.room(TWELVE) == (500_000, 150_000)
    assert sum(F.room(NINE)) * 100 // 718_000 == 66
    assert sum(F.room(TWELVE)) * 100 // 817_587 == 79
    assert F.room(None) == F.room(NINE)
    assert F.room(EIGHT) == F.room(NINE)  # (the 9.2"'s sibling: as many pixels, one family)
    assert F.tried(NINE) and F.tried(TWELVE) and F.tried(EIGHT) and not F.tried(None)
    for model in F.models():
        clip, logo = F.room(model)
        assert 0 < clip <= 500_000 and 0 < logo <= 150_000, model.id
        if not F.tried(model):
            assert clip <= 360_000 and logo <= 120_000, model.id
    assert F.room(get_model("turing-5.2-usb")) == F.room(NINE)  # (as many pixels: 720 x 1280)
    assert F.room(get_model("turing-2.8-usb")) == (90_000, 30_000)  # (480 x 480: a quarter)
    assert F.kilobytes(593_754) == "580 KB" and F.kilobytes(100) == "1 KB"


def test_the_clip_is_what_the_panels_decoder_plays():
    try:
        ffmpeg = find_ffmpeg()
    except DeviceError:
        pytest.skip("no ffmpeg")
    model = get_model("turing-2.8-usb")  # small: quick
    data = F.clip_h264(model, ffmpeg)
    assert slice_count(data) == bootscreen.CLIP_FRAMES == 44  # one slice a picture
    kinds = [data[start] & 0x1F for start, _end in nal_units(data)]
    assert kinds.count(7) == 4 and kinds.count(5) == 4  # a sequence every 11 pictures
    assert len(data) <= F.room(model)[0]
    assert "sliced-threads=0" in " ".join(F.clip_command("ffmpeg", (480, 480)))
    data = F.clip_h264(model, ffmpeg, design="night")  # a longer loop: more sequences
    assert slice_count(data) == bootscreen.design_of("night").frames == 88
    assert [data[start] & 0x1F for start, _end in nal_units(data)].count(7) == 8


def _first_luma(ffmpeg, data):
    import subprocess

    raw = subprocess.run([ffmpeg, "-v", "error", "-i", "-", "-frames:v", "1", "-f", "rawvideo",
                          "-"], input=data, capture_output=True, check=True).stdout  # fmt: skip
    return numpy.frombuffer(raw, numpy.uint8)[: len(raw) * 2 // 3]  # (its values as they are)


def test_a_full_range_panel_gets_its_clip_in_full_range():
    """The 9.2" and the 12.3" show every stream as full range: their clip is made so, its black
    0, not the 16 of limited range they showed as grey."""
    try:
        ffmpeg = find_ffmpeg()
    except DeviceError:
        pytest.skip("no ffmpeg")
    command = " ".join(F.clip_command("ffmpeg", (480, 480), full=True))
    assert "out_range=full" in command and "video_full_range_flag=1" in command
    assert "out_range=full" not in " ".join(F.clip_command("ffmpeg", (480, 480)))
    model = get_model("turing-2.8-usb")
    frames = bootscreen.standby_frames(model, 11, design="dark")
    limited = _first_luma(ffmpeg, F.clip_h264(model, ffmpeg, frames))
    full = _first_luma(ffmpeg, F.clip_h264(model, ffmpeg, frames, full=True))
    assert limited.min() >= 14 and full.min() <= 2


def test_a_clip_is_made_as_finely_as_fits_its_room(monkeypatch):
    """At crf 20 where that fits the clip's room on the panel (360,000 bytes on the 9.2"), and
    never finer: at 17 Circuit's clip was 715 KB for 395, with nothing to see for it. Else
    coarser: first at the crf guessed from how much too large it was (a clip gets about 13 %
    smaller with each step), then a step at a time until it is the finest whole step that
    fits; then the three quarters of a step finer than that, made at once, and the finest of
    them that fits (since 0.9.5: a whole step left up to 13 % of the room unused). Never
    coarser than 26, which would show: a look that needs more (Levels) is not made."""
    sizes, asked = {}, []

    def run(command, **kwargs):
        crf = float(command[command.index("-crf") + 1])
        asked.append(crf)
        return SimpleNamespace(returncode=0, stdout=b"\x00" * sizes[crf], stderr=b"")

    def made(model, **now):
        sizes.clear()
        sizes.update({float(crf[1:].replace("_", ".")): size for crf, size in now.items()})
        asked.clear()
        return len(F.clip_h264(model, "ffmpeg", [Image.new("RGB", (16, 16))]))

    def were(*whole, below):
        """The whole steps asked in turn, then the quarters below the whole step that fit (made
        at once: in any order)."""
        quarters = sorted(below - quarter for quarter in F.CLIP_QUARTERS)
        return asked[: len(whole)] == list(whole) and sorted(asked[len(whole) :]) == quarters

    monkeypatch.setattr(F.subprocess, "run", run)
    monkeypatch.setattr("libre_panel.devices.h264.slice_count", lambda data: 1)
    assert made(NINE, c20=352_645) == 352_645 and asked == [20]  # it fits: no finer
    assert F.CLIP_QUARTERS == (0.75, 0.5, 0.25)
    # (Aurora's sizes on the 9.2": 25 is the guess, 24 is 716 bytes too large, and 24.25 fits)
    assert made(NINE, c20=652_208, c25=318_482, c24=360_716,
                c24_25=349_368, c24_5=339_628, c24_75=327_997) == 349_368  # fmt: skip
    assert were(20, 25, 24, below=25)
    # (on end: 26 left 13 % of the room, 25.25 leaves 3 %)
    assert made(NINE, c20=796_787, c26=313_126, c25=361_140,
                c25_25=347_820, c25_5=335_368, c25_75=326_080) == 347_820  # fmt: skip
    assert were(20, 26, 25, below=26)
    # the guess two steps too coarse: finer until one does not fit; no quarter fits there
    assert made(NINE, c20=652_208, c25=300_000, c24=340_000, c23=359_000, c22=400_000,
                c22_25=390_000, c22_5=375_000, c22_75=364_000) == 359_000  # fmt: skip
    assert were(20, 25, 24, 23, 22, below=23)
    # the guess too fine: coarser, a step at a time; then the one quarter that fits
    assert made(NINE, c20=652_208, c25=380_000, c26=350_000,
                c25_25=372_000, c25_5=364_000, c25_75=357_000) == 357_000  # fmt: skip
    assert were(20, 25, 26, below=26)
    # (the next step after 20 is tried once, not again as "one finer"; Circuit's quarters)
    assert made(NINE, c20=394_704, c21=319_625,
                c20_25=374_619, c20_5=355_625, c20_75=337_121) == 355_625  # fmt: skip
    assert were(20, 21, below=21)
    with pytest.raises(DeviceError) as refused:  # Levels' sizes: too much at the coarsest
        made(NINE, c20=1_084_528, c26=584_548)
    assert str(refused.value) == TOO_FINE and asked == [20, 26]
    assert made(TWELVE, c20=477_724) == 477_724 and asked == [20]  # the 12.3" has more room
    # (Circuit's on the 12.3": not a quarter fits)
    assert made(TWELVE, c20=602_822, c22=410_260, c21=492_450,
                c20_25=571_152, c20_5=545_953, c20_75=521_002) == 492_450  # fmt: skip
    assert were(20, 22, 21, below=21)


# -- on the panel ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["/usr/data/boot.jpg", "usr/data/../boot.jpg", "media/logo.jpg", "usr/data/", "boot.jpg",
     "usr/data/a b.jpg", "usr/data\\boot.jpg", "usr/data/bööt.jpg", ""],
)  # fmt: skip
def test_only_plain_files_below_usr_data_are_written(name):
    with pytest.raises(DeviceError):
        F.checked_name(name)
    assert F.checked_name("usr/data/boot.jpg") == b"usr/data/boot.jpg"


LOGO, CLIP = (98, F.BOOT_LOGO, 0), (98, F.STANDBY_CLIP, 0)  # a file's size asked


def test_a_file_is_asked_for_before_and_after_it_is_written():
    """What the panel has of the file and of the other one, the file, and its size again; at
    most 1 MiB, the most the command takes, and never more than the file's room on the model
    (the files made here never are: nothing else gets through); and only the two files: no
    other name below usr/data (the panel's own settings are there) is written or taken off."""
    panel = FakePanel()
    files = F.PanelFiles(panel, NINE)
    with pytest.raises(DeviceError, match="1 MiB"):
        files.write(F.BOOT_LOGO, bytes(F.MAX_FILE + 1))
    with pytest.raises(DeviceError):
        files.write(F.BOOT_LOGO, b"")
    with pytest.raises(DeviceError, match="120001 bytes are more than the 120000 it gets"):
        files.write(F.BOOT_LOGO, bytes(120_001))
    with pytest.raises(DeviceError, match="360001 bytes are more than the 360000 it gets"):
        files.write(F.STANDBY_CLIP, bytes(360_001))
    with pytest.raises(DeviceError, match="more than the 360000 it gets"):
        files.write(F.STANDBY_CLIP, bytes(492_450))  # (a 12.3"'s clip: not on a 9.2")
    with pytest.raises(DeviceError, match="150001 bytes are more than the 150000 it gets"):
        F.PanelFiles(panel, TWELVE).write(F.BOOT_LOGO, bytes(150_001))
    for name in ("usr/data/app.cfg", "usr/data/boot.sign", "usr/data/other.jpg"):
        with pytest.raises(DeviceError, match="only the boot logo and the standby clip"):
            files.write(name, b"x")
        with pytest.raises(DeviceError, match="only the boot logo and the standby clip"):
            files.delete(name)
    assert panel.sent == []
    files.write(F.BOOT_LOGO, b"x" * 1000)
    assert panel.files == {F.BOOT_LOGO: b"x" * 1000}
    assert panel.sent == [LOGO, CLIP, (40, F.BOOT_LOGO, 1000), LOGO]
    panel.sent.clear()
    files.write(F.STANDBY_CLIP, b"c" * 2000)
    assert panel.sent == [CLIP, LOGO, (40, F.STANDBY_CLIP, 2000), CLIP]
    for never in (11, 12, 13):
        with pytest.raises(DeviceError, match="never sent"):
            files._command(never)


def test_a_file_the_panel_did_not_take_is_said_and_nothing_more_is_sent():
    """A 9.2" that was full took no file any more: for five days it answered each write at
    once and kept what it had, whether the new file was larger or smaller. That is said, in
    so many words, and nothing is taken away to be written again (0.9.1 did so, and that 9.2"
    has kept restarting since)."""
    old = b"o" * 700
    panel = FakePanel({F.STANDBY_CLIP: old}, refuses=True)
    files = F.PanelFiles(panel, NINE)
    for new in (b"n" * 710, b"n" * 500):
        with pytest.raises(DeviceError) as told:
            files.write(F.STANDBY_CLIP, new)
        assert str(told.value) == NOT_TAKEN
    with pytest.raises(DeviceError) as told:  # (a boot logo: none before, none after)
        files.write(F.BOOT_LOGO, b"l" * 100)
    assert str(told.value) == NOT_TAKEN
    assert panel.files == {F.STANDBY_CLIP: old}
    assert [cmd for cmd, *_ in panel.sent] == [98, 98, 40, 98] * 3  # (no 42, ever)


def test_a_file_of_the_size_the_panel_has_is_told_by_the_time_it_took(monkeypatch):
    """Where the panel has a file of the new one's size, the size says nothing: five times a
    clip was said to be on a 9.2" that had kept the one it had. A write takes its time (1.2
    to 2.9 s for 700 KB on that panel) and one the panel does not take is over when the
    transfer is (30 to 50 ms), so a file that was answered faster than any write is was not
    taken. A small file's transfer says nothing (Dark's boot logo, smaller than any clip): it
    is taken as written. (The time is the test's own: what the panels here say a write or a
    refusal takes.)"""
    now = [0.0]
    monkeypatch.setattr(F, "time", SimpleNamespace(monotonic=lambda: now[0]))

    def passes(seconds):
        now[0] += seconds

    old, new = b"o" * 360_000, b"n" * 360_000  # (the largest clip a 9.2" gets)
    kept = FakePanel({F.STANDBY_CLIP: old}, refuses=True)
    # as slowly as that 9.2" turned a file down: 700 KB in 50 ms, so these in 26
    kept.wait, kept.refusal = passes, 0.026
    with pytest.raises(DeviceError) as told:
        F.PanelFiles(kept, NINE).write(F.STANDBY_CLIP, new)
    assert str(told.value) == NOT_TAKEN and kept.files[F.STANDBY_CLIP] == old
    assert [cmd for cmd, *_ in kept.sent] == [98, 98, 40, 98]  # (and nothing after)
    # twice as fast as that 9.2" wrote anything (149 KB in under 220 ms, with what followed)
    writes = FakePanel({F.STANDBY_CLIP: old}, rate=1_400_000)
    writes.wait = passes
    F.PanelFiles(writes, NINE).write(F.STANDBY_CLIP, new)
    assert writes.files[F.STANDBY_CLIP] == new
    small = FakePanel({F.BOOT_LOGO: b"o" * 1000}, refuses=True)  # (kept, and not told)
    small.wait = passes
    F.PanelFiles(small, NINE).write(F.BOOT_LOGO, b"n" * 1000)
    assert small.files[F.BOOT_LOGO] == b"o" * 1000
    assert F.unwritten(706_912, 0.050) and not F.unwritten(706_912, 1.25)
    assert F.unwritten(360_000, 0.040) and not F.unwritten(360_000, 0.6)
    assert not F.unwritten(149_040, 0.22)  # (the fastest write in that panel's log)
    # every clip is told so, the smallest too (Dark's on a 9.2" on end, turned round); Dark's
    # boot logos are not
    assert F.unwritten(44_206, 0.003) and not F.unwritten(44_206, 0.048)
    assert not F.unwritten(14_289, 0.001) and not F.unwritten(21_952, 0.001)
    # each write of 0.9.4rc1 on a sound 9.2" and 12.3", and how long the panel took: all
    # written, the quickest (44,931 bytes in 48 ms) at under a quarter of the rate
    measured = [
        (319_122, 0.568), (113_393, 0.209), (44_931, 1.764), (44_931, 0.048), (113_393, 0.195),
        (319_122, 0.564), (485_481, 1.306), (485_481, 1.115), (141_864, 0.321), (141_864, 0.157),
    ]  # fmt: skip
    assert not [write for write in measured if F.unwritten(*write)]
    assert F.unwritten(44_931, 0.011) and F.unwritten(141_864, 0.035)  # (four times as fast)


def test_a_file_that_ended_early_is_taken_off_again():
    """A panel whose room ran out wrote a boot logo as far as the room went (16 KiB of 127 KB
    on a 9.2"), and was full from then on. So a file that ends early is taken off again at
    once (42), which leaves no panel full, and that is said; it is not written a second time.
    A clip takes the place of the one before it, so its room is that one's and what was free:
    one that is larger than both ends there, and goes the same way."""
    clip = b"c" * 700
    panel = FakePanel({F.STANDBY_CLIP: clip}, room=700 + 1638)
    with pytest.raises(DeviceError) as told:
        F.PanelFiles(panel, NINE).write(F.BOOT_LOGO, b"l" * 5000)
    assert str(told.value) == (
        "The boot logo did not fit: the panel took 1638 of 5000 bytes. It was taken off again, "
        "so that the panel is not left full; the factory logo shows at the next power-on."
    )
    assert panel.files == {F.STANDBY_CLIP: clip}
    assert panel.sent == [LOGO, CLIP, (40, F.BOOT_LOGO, 5000), LOGO, (42, F.BOOT_LOGO, 0), LOGO]
    full = FakePanel({F.STANDBY_CLIP: b"o" * 700, F.BOOT_LOGO: b"l" * 100}, room=804)
    with pytest.raises(DeviceError) as told:
        F.PanelFiles(full, NINE).write(F.STANDBY_CLIP, b"n" * 710)
    assert str(told.value) == (
        "The standby clip did not fit: the panel took 704 of 710 bytes. It was taken off again, "
        "so that the panel is not left full: the panel has no standby clip now. Dark is the "
        "smallest look."
    )
    assert full.files == {F.BOOT_LOGO: b"l" * 100}
    assert [cmd for cmd, *_ in full.sent] == [98, 98, 40, 98, 42, 98]  # (and no second 40)

    class Stuck(FakePanel):  # a panel that does not let the file go
        def command(self, cmd, args=b"", payload=b"", timeout_ms=2000):
            if cmd == 42:
                self.sent.append((cmd, self.name_of(args), 0))
                return bytes([cmd, 0xC8]) + bytes(510)
            return super().command(cmd, args, payload, timeout_ms)

    stuck = Stuck({F.STANDBY_CLIP: clip}, room=700 + 1638)
    with pytest.raises(DeviceError) as told:
        F.PanelFiles(stuck, NINE).write(F.BOOT_LOGO, b"l" * 5000)
    assert str(told.value) == (
        "usr/data/boot.jpg did not fit (the panel took 1638 of 5000 bytes) and could not be "
        "taken off again: 1638 bytes of it stay"
    )


def test_a_file_is_not_sent_where_both_would_be_more_than_the_panels_room():
    """A panel written to by 0.7.9 to 0.9.1 may hold a clip of 700 KB. A boot logo beside it
    would take the panel past the room Libre Panel uses there (on the 9.2" of issue 52 the
    first 16 KiB of one were the last that fitted): it is not sent, and the app says to write
    the clip again first, which makes it smaller. A file that is no larger than the one it
    replaces always goes: that is the way out."""
    panel = FakePanel({F.STANDBY_CLIP: b"o" * 701_830})
    files = F.PanelFiles(panel, NINE)
    with pytest.raises(DeviceError) as told:
        files.write(F.BOOT_LOGO, b"l" * 113_040)
    assert str(told.value) == (
        "Not written: with the standby clip that is on the panel (685 KB), the boot logo would "
        "be more than Libre Panel puts on this panel. Write the standby clip again first: it "
        "becomes smaller."
    )
    assert panel.sent == [LOGO, CLIP]  # asked, and nothing sent
    files.write(F.STANDBY_CLIP, b"n" * 319_625)  # smaller than the one there: it goes
    files.write(F.BOOT_LOGO, b"l" * 113_040)  # and beside that one the logo fits
    assert {name: len(data) for name, data in panel.files.items()} == {
        F.STANDBY_CLIP: 319_625, F.BOOT_LOGO: 113_040,
    }  # fmt: skip
    old_logo = FakePanel({F.BOOT_LOGO: b"o" * 491_507})  # the other way round
    with pytest.raises(DeviceError) as told:
        F.PanelFiles(old_logo, NINE).write(F.STANDBY_CLIP, b"n" * 319_625)
    assert str(told.value) == (
        "Not written: with the boot logo that is on the panel (480 KB), the standby clip would "
        "be more than Libre Panel puts on this panel. Write the boot logo again first: it "
        "becomes smaller."
    )
    twelve = FakePanel({F.STANDBY_CLIP: b"o" * 593_754})  # (what a 12.3" holds of 0.9.0)
    with pytest.raises(DeviceError, match=r"standby clip that is on the panel \(580 KB\)"):
        F.PanelFiles(twelve, TWELVE).write(F.BOOT_LOGO, b"l" * 141_864)
    F.PanelFiles(twelve, TWELVE).write(F.STANDBY_CLIP, b"n" * 492_450)
    F.PanelFiles(twelve, TWELVE).write(F.BOOT_LOGO, b"l" * 141_864)  # (634,314 of 650,000)
    assert all(cmd != 42 for cmd, *_ in panel.sent + old_logo.sent + twelve.sent)


def test_files_are_written_to_the_models_it_has_been_done_on(isolated_home, monkeypatch):
    """Start screens are written to the 9.2" and the 12.3", where it has been done and what
    fits is known (issue 52), and to the 8.8", the 9.2"'s sibling. How much room another panel
    of the family has is not known: for one (a 5.2" here), neither job opens the panel or makes
    anything, the lowest layer refuses too, and the app says so before it pauses the themes.
    What the panel holds is still asked and the boot logo still taken off. Writing there is its
    owner's to choose, each time (``untested``, which the app sends only as true after it has
    said what may happen), or the word in the environment's; the app says which models have been
    tried, which confirmed, and where to share how it went."""
    import threading

    from libre_panel import __version__
    from libre_panel.editor.server import make_server

    other = get_model("turing-5.2-usb")
    for word in (None, "1", "true", "AT-MY-OWN-RISK"):
        monkeypatch.delenv(F.WRITES_ENV, raising=False)
        if word is not None:
            monkeypatch.setenv(F.WRITES_ENV, word)
        assert not F.writes_on(other) and not F.writes_on(None), word
        assert F.writes_on(NINE) and F.writes_on(TWELVE) and F.writes_on(EIGHT), word
    monkeypatch.delenv(F.WRITES_ENV)
    written_to = {model.id for model in F.models() if F.writes_on(model)}
    assert written_to == {"turing-8.8-usb", "turing-9.2-usb", "turing-12.3-usb"} == set(F.ROOM)
    opened = []

    def opener(asked):
        opened.append(asked)
        return panel, asked or other

    def no_ffmpeg():
        raise AssertionError("nothing is made for a clip that is not written")

    panel = FakePanel({F.BOOT_LOGO: b"logo", F.STANDBY_CLIP: b"clip"})
    screen = F.StartScreen(opener=opener, ffmpeg=no_ffmpeg)
    for write in (lambda: screen.write_logo(other), lambda: screen.write_clip(60, other)):
        with pytest.raises(DeviceError) as refused:
            write()
        assert str(refused.value) == WRITES_OFF
    assert opened == [] and panel.sent == []  # the panel was not even opened
    # a panel whose model is known only once it is opened: opened, and nothing made or sent
    found = F.StartScreen(opener=opener, ffmpeg=lambda: "ffmpeg")
    for write in (found.write_logo, lambda: found.write_clip(60)):
        with pytest.raises(DeviceError) as refused:
            write()
        assert str(refused.value) == WRITES_OFF
    assert opened == [None, None] and panel.sent == []
    for files in (F.PanelFiles(panel, other), F.PanelFiles(panel)):  # nor the layer under them
        with pytest.raises(DeviceError) as refused:
            files.write(F.BOOT_LOGO, b"new logo")
        assert str(refused.value) == WRITES_OFF and panel.sent == []
    assert panel.files == {F.BOOT_LOGO: b"logo", F.STANDBY_CLIP: b"clip"}
    # its owner's word: the gate is passed (here, to an ffmpeg that is looked for after it)
    assert F.writes_on(other, untested=True) and F.writes_on(None, untested=True)

    def looked_for():
        raise LookupError("past the gate")

    with pytest.raises(LookupError, match="past the gate"):
        F.StartScreen(opener=opener, ffmpeg=looked_for).write_clip(60, other, untested=True)
    assert opened == [None, None] and panel.sent == []
    # what the panel holds, and its boot logo taken off: as ever
    assert screen.state(other)["boot_logo"] == 4
    panel.sent.clear()
    assert screen.remove_logo(other) == {"model": other.id, "boot_logo": 0}
    assert panel.sent == [(42, F.BOOT_LOGO, 0), (98, F.BOOT_LOGO, 0)]

    class Controls:
        def __init__(self):
            self.acts = []

        def act(self, action, value=None):
            self.acts.append(action)
            return {}

        def snapshot(self):
            return {"panel": {}}

    controls = Controls()
    srv = make_server(port=0, controls=controls)
    srv.editor_state.start_screen_jobs = screen
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        told = {m["id"]: m for m in request(port, "GET", "/api/start-screen")[1]["models"]}
        assert {name for name, model in told.items() if model["writes"]} == written_to
        assert told["turing-9.2-usb"]["room"] == {"clip": 360_000, "logo": 120_000}
        assert told["turing-12.3-usb"]["room"] == {"clip": 500_000, "logo": 150_000}
        assert told["turing-8.8-usb"]["room"] == told["turing-9.2-usb"]["room"]
        assert told["turing-5.2-usb"]["room"] == {"clip": 360_000, "logo": 120_000}
        for model in written_to:
            assert told[model]["unfit"] == ["levels"], model
        # (measured for its room too: a look that would be too fine is not offered there)
        assert told["turing-5.2-usb"]["unfit"] == list(F.UNFIT["turing-5.2-usb"])
        assert "levels" in told["turing-5.2-usb"]["unfit"]
        assert {name for name, model in told.items() if model["tried"]} == written_to
        confirmed = {name for name, model in told.items() if model["confirmed"]}
        assert confirmed == {"turing-9.2-usb", "turing-12.3-usb"} == set(F.CONFIRMED)
        state = request(port, "GET", "/api/start-screen")[1]
        assert state["report"] == F.REPORT_FORM and state["version"] == __version__
        panel.sent.clear()
        for action in ("logo", "clip"):
            status, data = request(port, "POST", "/api/start-screen",
                                   {"action": action, "model": other.id})  # fmt: skip
            assert (status, data) == (409, {"error": WRITES_OFF, "paused": False}), action
        assert controls.acts == [] and panel.sent == []  # the themes were not paused for it
        # its owner's word is true and nothing else
        for word in ("yes", 1, "true", None):
            asked = {"action": "logo", "model": other.id, "untested": word}
            status, data = request(port, "POST", "/api/start-screen", asked)
            assert (status, data) == (409, {"error": WRITES_OFF, "paused": False}), word
        assert controls.acts == [] and panel.sent == []
        status, data = request(port, "POST", "/api/start-screen", {"action": "state"})
        assert status == 200 and data["standby_clip"] == 4 and controls.acts == ["pause"]
        # with it, the boot logo is made for the 9.2"'s room by the pixels, and written
        panel.sent.clear()
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "model": other.id, "untested": True})  # fmt: skip
        assert status == 200 and data["model"] == other.id, data
        assert 0 < data["boot_logo"] == len(panel.files[F.BOOT_LOGO]) <= F.room(other)[1]
        assert [cmd for cmd, *_ in panel.sent][-3:] == [40, 98, 113]
        panel.files[F.BOOT_LOGO] = b"logo"
        monkeypatch.setenv(F.WRITES_ENV, F.WRITES_WORD)
        told = request(port, "GET", "/api/start-screen")[1]["models"]
        assert all(model["writes"] for model in told)
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()


def test_the_form_to_share_how_it_went_has_the_fields_the_app_fills_in():
    """The start screen card fills GitHub's issue form in (startscreen.js: the model, the
    version, the look and what was written): each is a field of the form, which is where
    REPORT_FORM leads."""
    import re
    from pathlib import Path

    assert F.REPORT_FORM.endswith("/issues/new?template=start_screen.yml")
    root = Path(__file__).resolve().parents[1]
    form = (root / ".github/ISSUE_TEMPLATE/start_screen.yml").read_text(encoding="utf-8")
    fields = re.findall(r"^    id: (\S+)$", form, flags=re.MULTILINE)
    assert {"model", "version", "look", "written"} <= set(fields), fields
    assert 'title: "Start screen: "' in form and "labels: [hardware]" in form


def test_the_boot_logo_is_written_shown_and_taken_away_again():
    panel = FakePanel({F.STANDBY_CLIP: b"clip"})
    answer = jobs(panel).write_logo()
    jpeg = panel.files[F.BOOT_LOGO]
    assert answer == {"model": NINE.id, "boot_logo": len(jpeg), "design": "libre-panel"}
    kinds = [cmd for cmd, *_ in panel.sent]
    # listed (all pieces), both sizes asked, written, checked, shown
    assert kinds == [99] * 20 + [98, 98, 40, 98, 113]
    assert panel.sent[22] == (40, F.BOOT_LOGO, len(jpeg)) and panel.closed
    panel.sent.clear()
    assert jobs(panel).remove_logo() == {"model": NINE.id, "boot_logo": 0}
    assert panel.sent == [(42, F.BOOT_LOGO, 0), (98, F.BOOT_LOGO, 0)]
    assert F.STANDBY_CLIP in panel.files  # nothing else is touched


def test_the_standby_clip_is_written_played_and_saved_as_what_the_panel_starts_with():
    try:
        find_ffmpeg()
    except DeviceError:
        pytest.skip("no ffmpeg")
    panel = FakePanel()
    small = get_model("turing-2.8-usb")
    answer = jobs(panel, small).write_clip(60, small, design="dark")
    clip = panel.files[F.STANDBY_CLIP]
    assert answer == {"model": small.id, "standby_clip": len(clip), "name": F.STANDBY_CLIP,
                      "design": "dark"}  # fmt: skip
    after = [entry for entry in panel.sent if entry[0] != 99]
    assert after == [
        CLIP, LOGO,  # what the panel has of both
        (40, F.STANDBY_CLIP, len(clip)), (98, F.STANDBY_CLIP, 0),
        (15, bytes([30]), 0),  # the rate of a cold start
        (110, F.STANDBY_CLIP, 0),  # played, its name (never an empty one) in the settings
        (125, bytes([61, 2, 0, 0, 0, 0]), 0),  # saved: brightness, start with the clip
    ]  # fmt: skip


@pytest.mark.parametrize(
    "model, video_range, full",
    [("turing-12.3-usb", "auto", True), ("turing-12.3-usb", "limited", False),
     ("turing-8.8-usb", "auto", False), ("turing-8.8-usb", "full", True)],
)  # fmt: skip
def test_the_clip_is_made_in_the_range_the_panel_shows(monkeypatch, model, video_range, full):
    made = []
    monkeypatch.setattr(F, "clip_h264", lambda found, ffmpeg, **how: made.append(how) or b"c")
    found = get_model(model)
    screen = F.StartScreen(opener=lambda asked: (FakePanel(), found), ffmpeg=lambda: "ffmpeg")
    screen.write_clip(50, found, design="aurora", video_range=video_range)  # (no ffmpeg needed)
    assert made == [{"orientation": "landscape", "design": "aurora", "full": full}]


def test_a_listing_without_a_header_is_read_as_the_9_2_sends_it():
    """The 9.2" sends each piece of the listing as it is, beginning with zeros: no command
    number in front (\"command 99: unexpected reply 00 00 ...\" stopped every write)."""
    panel = FakePanel({F.BOOT_LOGO: b"j", F.STANDBY_CLIP: b"c"}, listing="bare")
    assert F.PanelFiles(panel).listing() == ["boot.jpg", "standby.h264"]
    silent = FakePanel({}, listing="none")
    assert F.PanelFiles(silent).listing() == []  # nothing in it: no names, and no error
    answer = jobs(silent).write_logo()  # the boot logo is written all the same
    assert answer["boot_logo"] == len(silent.files[F.BOOT_LOGO])


def test_the_listing_gives_the_names_the_panel_has():
    assert F.names_in(b"boot.jpg\0standby.h264\0\0img\0app.cfg\0boot.jpg\0") == [
        "boot.jpg", "standby.h264", "app.cfg"
    ]  # fmt: skip
    panel = FakePanel({F.BOOT_LOGO: b"j", F.STANDBY_CLIP: b"c"})
    state = jobs(panel).state()
    assert state == {"model": NINE.id, "files": ["boot.jpg", "standby.h264"], "boot_logo": 1,
                     "standby_clip": 1, "room": {"clip": 360_000, "logo": 120_000}}  # fmt: skip


# -- in the app --------------------------------------------------------------------------------


def test_the_app_shows_the_pictures_for_each_usb_panel(server, isolated_home):  # noqa: F811
    status, data = request(server, "GET", "/api/start-screen")
    assert status == 200 and [m["id"] for m in data["models"]] == [m.id for m in F.models()]
    assert [m["id"] for m in data["models"] if m["measured"]] == ["turing-9.2-usb"]
    assert data["names"] == {"logo": F.BOOT_LOGO, "clip": F.STANDBY_CLIP}
    assert data["designs"] == LOOKS and LOOKS[0] == "libre-panel" and len(LOOKS) >= 7
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-4.6-usb&what=logo&w=480&design=night"
    )
    assert status == 200 and Image.open(io.BytesIO(png)).size == (480, 160)
    path = "/api/start-screen/picture?model=turing-4.6-usb&what=logo&w=480&design=sunset"
    assert request(server, "GET", path)[0] == 404
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-4.6-usb&what=logo&w=480"
    )
    assert status == 200 and Image.open(io.BytesIO(png)).size == (480, 160)
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-2.8-usb&what=clip&w=240"
    )
    picture = Image.open(io.BytesIO(png))
    assert status == 200 and getattr(picture, "n_frames", 1) == 44
    status, jpeg = request(server, "GET", "/api/start-screen/file?model=turing-9.2-usb&what=logo")
    assert status == 200 and Image.open(io.BytesIO(jpeg)).size == (480, 1920)
    assert request(server, "GET", "/api/start-screen/picture?model=turing-3.5&what=logo")[0] == 404
    from libre_panel.config import set_config_value

    set_config_value("device.orientation", "portrait")  # a bar on end: they stand upright
    status, data = request(server, "GET", "/api/start-screen")
    assert data["orientation"] == "portrait"
    assert [m["size"] for m in data["models"] if m["id"] == "turing-9.2-usb"] == [[480, 1920]]
    status, png = request(
        server, "GET", "/api/start-screen/picture?model=turing-9.2-usb&what=logo&w=240&o=portrait"
    )
    assert status == 200 and Image.open(io.BytesIO(png)).size == (240, 960)


def test_the_app_writes_only_when_asked_and_pauses_the_panel_for_it(isolated_home, monkeypatch):
    import threading

    from test_editor_server import token_of  # noqa: F401 (the token of the server)

    from libre_panel.editor.server import make_server

    class Controls:
        def __init__(self):
            self.acts = []

        def act(self, action, value=None):
            self.acts.append(action)
            return {}

        def snapshot(self):
            return {"panel": {}}

    controls = Controls()
    srv = make_server(port=0, controls=controls)
    panel = FakePanel()
    srv.editor_state.start_screen_jobs = jobs(panel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        status, data = request(port, "POST", "/api/start-screen", {"action": "logo"})
        assert status == 200 and data["paused"] is True and data["boot_logo"] > 0
        assert controls.acts == ["pause"] and F.BOOT_LOGO in panel.files
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "design": "circuit"})  # fmt: skip
        assert status == 200 and data["design"] == "circuit"
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "design": "sunset"})  # fmt: skip
        assert status == 400 and "no look" in data["error"]
        status, data = request(port, "POST", "/api/start-screen", {"action": "flash"})
        assert status == 400
        status, data = request(port, "POST", "/api/start-screen",
                               {"action": "logo", "model": "turing-3.5"})  # fmt: skip
        assert status == 400  # no TURZX USB panel

        def broken(asked):
            raise DeviceError("the panel is busy")

        srv.editor_state.start_screen_jobs = F.StartScreen(opener=broken)
        status, data = request(port, "POST", "/api/start-screen", {"action": "state"})
        assert status == 409 and data["error"] == "the panel is busy" and data["paused"] is True
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()
