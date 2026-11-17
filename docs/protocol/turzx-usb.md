# TURZX / Turing V1.x USB protocol

Applies to the USB panels with VID `0x1CBE`: 2.8" round (`0x0028`), 4.6"
(`0x0046`), 5.2" (`0x0050`), 8" (`0x0080`), 8.8" (`0x0088`), 9.2" (`0x0092`),
12.3" (`0x0123`).

Sources: the GPL-3.0 reference implementation in
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(`library/lcd/lcd_comm_turing_usb.py`) and measurements on a 9.2" panel made
in the SPUR II project. Statements marked **verified** were observed on that
panel; everything else comes from the reference and still needs confirmation
per model. The JPEG stills follow the notes of two other projects (see
*JPEG stills (101)*).

## Transport

- One interface (0), class `FF`, bulk **OUT `0x01`** and bulk **IN `0x81`**,
  512-byte packets, USB 2.0 high speed. **verified**
- On Windows the device binds to WinUSB through its MS OS descriptor; libusb can
  open it without Zadig. **verified**
- The vendor app keeps the device open exclusively; quit it first. **verified**

## Command packet

Every command is exactly 512 bytes:

| Bytes (plain text) | Content |
|---|---|
| 0 | command id |
| 1 | 0 |
| 2–3 | magic `1A 6D` |
| 4–7 | milliseconds since local midnight, little endian |
| 8–499 | arguments (command specific) |

The 500 plain-text bytes are zero-padded to 504 and encrypted with **DES-CBC,
key = IV = `slv3tuzx`**. The 504 cipher bytes go into a 512-byte buffer whose
last two bytes are the trailer `A1 1A`. A payload (PNG, H.264) follows the
packet **in the same bulk transfer**. **verified** byte for byte against the
reference and the vendor app.

## Replies

The panel answers every command on bulk IN. Byte 0 echoes the command id,
`0xC8` (in byte 1, or byte 8 for some commands) means *accepted* — not
*displayed*.

**Zero-length packet (important).** Replies are exactly 512 bytes, which equals
`wMaxPacketSize`, so USB terminates them with a zero-length packet. Reading
exactly 512 bytes leaves that ZLP in the pipe; every following reply is then
off by one, the IN pipe fills up and the device stalls bulk OUT. **Read up to
1024 bytes** to consume it (the reference library instead flushes with a 100 ms
timeout per command). Check that byte 0 echoes the id you sent. **verified**

After a crash, stale replies can still be queued: drain IN until timeout, then
send a sync and check the echo (up to three attempts). **verified**

## Commands

| Id | Meaning | Arguments / notes |
|---|---|---|
| 10 | sync / hello | reply `0A C8` + ASCII `turzx_00` **verified** (and bytes that change with every connection on the 9.2"; a 9.2" names itself `turzx_0001_0015` (issue 52), as the 5.2" does at `[8:]`, which jijiechen/turing-monitor reads as its firmware version; Libre Panel logs what follows `turzx_` when it connects) |
| 14 | brightness | `[8]` = 0–102 (percent × 1.02) |
| 15 | frame rate | `[8]` = fps; also sets the panel's own playback rate |
| 17 | H.264 block size | reply 0 on the 9.2" → default 202752 **verified** |
| 100 | storage info | all zero: no card in the 9.2" **verified** |
| 101 | JPEG frame | `[8:12]` = size, big endian; payload = baseline JPEG, to the layer under 102. On the 9.2" only 464 columns wide (see *JPEG stills (101)*) |
| 102 | PNG frame / overlay | `[8:12]` = size, big endian; payload = PNG |
| 110 | start local video playback | `[8:12]` = name length, `[16:]` = name. Clears the framebuffer (needed before streaming) and copies the name into the panel's settings **in memory** **verified** |
| 111 | stop local playback | **verified** |
| 112 | local playback running? | **verified** |
| 121 | H.264 block | `[8:12]` = size, `[12]` = 1 on the last block of a clip |
| 122 | queue depth | reply `[8]` = blocks waiting **verified** |
| 123 | stop stream | **verified** |

Commands that change what the panel keeps are **never sent while the panel
shows themes**. A few are sent for the panel's own screens, and only when the
person asks for it in *Settings → Panel → Start screen* (see *The panel's own
screens* below): 40, 42, 98, 99, 113 and 125. Never sent at all: 13 (set
rotation and save the settings), 38, 39, 41, 100, firmware. 11 restarts the
panel (back after about 5 s; the only fix for a hung video decoder besides
replugging); Libre Panel sends it for that and nothing else (see *A hung
decoder*). 12 **halts** the panel until it loses power: never send it.

## Frames

- The framebuffer is **480×1920 portrait** on the 9.2". A 1920×480 landscape
  design is rotated with **ROTATE_270** before sending. **verified**
- The bezel hides the framebuffer's last **18 columns** (its right edge): the
  visible area is 1920×462, the reference library's size. In landscape the
  strip is the frame's top 18 rows; in portrait (ROTATE_180) its left 18
  columns. Measured with a 1 px ruler; the catalog carries it as data
  (`hidden`) for layouts and the editor, and nothing crops a PNG frame (a
  JPEG still is 464 columns wide, 2 into the strip; see below).
  **verified**
- PNG frames must be **colour type 6 (RGBA)**. With RGB the decoder is off by
  one byte per pixel and tiles the image four times. **verified**
- A full PNG frame takes about 87 ms on the device → roughly **9 fps** for full
  frames. **verified**

## JPEG stills (101)

Libre Panel sends stills as JPEG where `[device] still` says so: `"jpeg"` on
any TURZX USB panel, and `"auto"` on the 5.2". The facts come from
[sjcsoftware/turzx-linux](https://github.com/sjcsoftware/turzx-linux/blob/main/docs/protocol.md)
(verified there on a 9.2", firmware `turzx_0001_0015`) and
[jijiechen/turing-monitor](https://github.com/jijiechen/turing-monitor/blob/main/docs/PROTOCOL.md)
(5.2"); Libre Panel has not been seen sending them on hardware yet.

- **The packet** is built like 102: `[8:12]` = the payload's size, big
  endian; a baseline JPEG follows the packet in the same transfer, below
  1 MiB like every payload. The panel decodes it in hardware (a PNG in
  software: about 9 full frames a second).
- **The width is whole blocks of 16.** On the 9.2" the layers are **464**
  pixels wide (29 × 16, the alignment of the hardware JPEG decoder), not
  480. A 480-wide JPEG is decoded with the wrong stride: the picture is
  roughly right, but its last rows wrap onto the first ~70 (garbage with
  diagonal streaks at one end) and the other end is never updated. That is
  why 101 "did not display correctly" on the 9.2" in SPUR II. About 462 of
  the 464 columns show behind the bezel, which fits the 18 columns measured
  here: 480 − 18 = 462, rounded up to whole blocks = 464. So Libre Panel
  cuts the native frame to its first 464 columns (464–479 lie behind the
  bezel) and never stretches it. Where nothing is hidden the width stays
  the framebuffer's: 720 on the 5.2" and the 12.3", whole blocks already
  (turzx-linux suggests a width rounded up to a multiple of 16 for the
  other models, which this gives).
- **Subsampling:** 4:4:4 and 4:2:0 both decode once the width is right
  (4:2:2 untested). Libre Panel uses 4:4:4 at quality 90 for crisp
  coloured text, as turzx-linux does; a frame that would be larger than
  1 MiB takes quality 75, 60 or 45.
- **Two layers.** A JPEG goes to the base layer, where the video plays; the
  PNG of 102 is an overlay above it with per-pixel alpha, and it **keeps its
  last content, even across reconnects**: an old opaque PNG hides every
  later JPEG. Before its first JPEG on each connection Libre Panel sends a
  fully transparent PNG of the framebuffer's size, as the video mode does
  (and as the vendor app does before streaming).
- **Speed:** on the 9.2" turzx-linux measured about 20 fps for 800 KB
  frames of noise and more than 45 fps for simple ones (12–16 MB/s), where
  full PNG frames give about 9 fps. turing-monitor measured about 8 fps
  with 30 KB frames on the 5.2".
- **The 5.2" shows no PNG.** It acknowledges 102 and shows nothing
  (turing-monitor); 101 is its only still path, so `"auto"` takes JPEG
  there. PNG stays the default on the others, where it is proven (9.2",
  12.3").

## Two layers: H.264 video + PNG overlay (smooth path)

The panel composes two layers **verified**:

1. **Background video** — H.264 sent in blocks with command 121, decoded by
   the panel's hardware decoder.
2. **Overlay** — an RGBA PNG sent with command 102, drawn over the video with
   transparency. The vendor app's "1 fps" is simply how often it updates this
   overlay.

The PNG path alone is limited by the ~60 ms the panel needs per full frame.
The decoder is not: SPUR II has driven the 9.2" at 50 fps around the clock
this way (50.0 fps median, 0 dropped blocks, 0 USB errors, about 250 KB/s).
Libre Panel's video mode ([configuration](../CONFIGURATION.md#video)) puts the
whole frame into the video and keeps the overlay transparent.

### Start

`10 → 110(name) → 111 → 112 → 14 → 102 (fully transparent PNG) → 15 → 17`

Without 110 the screen stays black. 110 is really "play a local clip": it
clears the framebuffer and copies its name into the panel's settings, and 111
stops the playback again at once. **Never send 110 with an empty name**: a
later save of the settings (13 or 125, e.g. by the vendor app) would erase
the panel's standby clip. The vendor app also sends 13 and 42; neither is
needed. With this sequence nothing is saved: after a restart the panel
behaves exactly as before. **verified** (9.2", 1501 blocks in 30 s, then a
restart with the standby clip unchanged)

The overlay must be fully transparent (alpha 0); opaque black hides the video.

### Encoder

x264, Constrained Baseline, yuv420p, Annex B:

```
-c:v libx264 -profile:v baseline -preset superfast -tune zerolatency -threads 1
-crf 25 -maxrate 2M -bufsize 2M
-x264-params sliced-threads=0:bframes=0:scenecut=0:keyint=500:min-keyint=500:ipratio=2.0:repeat-headers=1
```

- **One slice per picture.** `-tune zerolatency` turns slice threads on; the
  panel acknowledges multi-slice pictures but shows nothing.
- **Keyframes every 3–10 s** (keyint = seconds × fps). Each keyframe
  re-quantises static areas; at 1 s and below flat backgrounds visibly pulse.
  `repeat-headers=1` lets the decoder join at any keyframe.
- ffmpeg rotates (`transpose=1` = PIL's `ROTATE_270`); the stream is
  bit-identical to rotating before encoding.
- **How the frames reach ffmpeg.** Libre Panel writes each frame into ffmpeg's
  stdin as raw video of the theme's size, packed into the pipe by Pillow from
  ffmpeg's writer thread (no copy of the frame as bytes, the render loop free
  meanwhile), as `-pix_fmt rgb0`: the four bytes a pixel Pillow keeps, the
  fourth (the canvas's alpha) left aside, as RGB never showed it
  (`[device.video] pixels = "rgb"`: `rgb24`, three bytes, the feed of before).
- **Who converts the colours.** The processor by default: after `transpose`,
  swscale turns RGB into BT.601 in limited range and the encoder gets NV12
  (`-pix_fmt nv12`; x264 `yuv420p`). For full range (`[device] video_range`,
  below) the filter graph ends in
  `scale=out_range=full:out_color_matrix=bt601,format=nv12` (red 255,0,0 comes
  out as Y 76, U 85, V 255; black as 0). The YUV from rgb0 is the same, byte for
  byte, as from rgb24 (FFmpeg 6.1 and 8.1). The graphics card's way, where it
  converts (`pixels = "card"`, or `"auto"` once the check below found it right),
  told the matrix and the range explicitly in each case:
  - **AMD** (`h264_amf`): `-init_hw_device amf=amf -filter_hw_device amf`, then
    `transpose=1,format=rgba,hwupload,vpp_amf=format=nv12:color_profile=bt601:color_range=studio:primaries=smpte170m:trc=smpte170m`
    (`color_range=full` for full range). AMF's scaler converts on the card and
    the encoder takes its surfaces as they are. `format=rgba` because the AMF
    frames take RGBA, BGRA or BGR0 but not RGB0 (`hwcontext_amf.c`), and
    `vf_amf_common.c` takes the same formats in as it puts out. Left to itself
    (rgb0 straight into `h264_amf`, as this path was before) the colour check
    measured AMD's encoder converting with BT.709 and a full-range tag: the
    stream decoded as `yuvj420p (pc, bt709)`, a **matrix shift** of 27 in Y of
    green against the processor's.
  - **Intel** (`h264_qsv`): `-init_hw_device qsv=qsv -filter_hw_device qsv`, then
    `transpose=1,format=bgra,setrange=full,vpp_qsv=format=nv12:out_range=tv:out_color_matrix=smpte170m:out_color_primaries=smpte170m:out_color_transfer=smpte170m`
    (`out_range=pc` for full range; `setrange=full` tells VPP its RGB input is
    0-255, `vf_vpp_qsv.c` reads it from the frame).
  - **NVIDIA** (`h264_nvenc`) has no filter that converts with a chosen matrix
    (`scale_cuda` keeps the matrix, `colorspace_cuda` changes only the range),
    so the encoder's own converter takes rgb0 (`NV_ENC_BUFFER_FORMAT_ABGR`),
    which FFmpeg's `nvenc.c` marks as BT.601 in limited range; on a panel in full
    range the processor converts for it.
  The filter graphs and options are as FFmpeg 8.1 (the downloads' build) takes
  them; whether they run on the card only that card shows (**unverified** on
  AMD, Intel and NVIDIA). A card's encoder that ends before its first picture
  converting itself is started again with the processor's conversion, by
  itself, before "auto" leaves it out for x264; `video-state.json` keeps that
  for a week per encoder and ffmpeg.
- **Every stream says what it is.** Each encoder is told
  `-colorspace smpte170m -color_primaries smpte170m -color_trc smpte170m -color_range tv`
  (`pc` for full range), and on every path, x264 too, the bitstream filter
  `-bsf:v h264_metadata=video_full_range_flag=0:colour_primaries=6:transfer_characteristics=6:matrix_coefficients=6`
  (`video_full_range_flag=1` for full range) writes it into the SPS's VUI
  whatever the encoder wrote there, so a player on the PC shows the stream as
  the panel does. It changes the VUI only: the same NAL units in the same
  order (SPS, PPS, SEI, IDR), one slice per picture, SPS and PPS before every
  keyframe (checked with ffprobe: `color_range tv`/`pc`, `color_space
  smpte170m`).
- **The range (`[device] video_range`).** The decoder of a panel shows the
  values either as limited range (16-235) or as full range (0-255), whatever
  the stream says. The 12.3" shows them as full range: its video looked washed
  out (black grey) in limited range with the processor's and the card's
  conversion alike, while its stills were right, so its model says
  `video_range = "full"` and "auto" makes full range for it; the others get
  limited range. *Is black too light on the panel? Compare* shows a panel the
  test picture in both, A and B, and writes the answer.
- **Checking the colours** (Settings → Panel → *Check the colours*,
  `devices/colour_check.py`). The panel's decoder applies one fixed matrix to
  every stream, BT.601, whatever the stream says of its colours: equal YUV in the
  stream is equal colours on the panel. So the check compares YUV, measured on
  the PC and away from the panel: a test picture at the size of the panel's
  frames, of large flat patches (black, five greys, white, pure red, green and
  blue, the themes' accent `#2EE6D6` and violet `#A78BFA`, an orange, a skin
  tone), goes ten times through the very command the display builds (its
  encoder settings, its `transpose`, its range), for a card's encoder once the
  card's way and once the processor's, for x264 fed rgb0 and rgb24, for every
  encoder of the card ffmpeg can start here (the probe "auto" goes by: the video
  mode's own command, whose last line from ffmpeg the card shows for an encoder
  passed over) and for x264, one run after the other, each with a time limit.
  ffmpeg decodes each stream to raw video in the decoder's own pixel format,
  converting nothing (the result names it: yuv420p, or yuvj420p for a stream
  that says it is full range, read the same); the turn is undone and the mean
  Y, U and V of the middle of every patch (half its width and height, at least
  8 pixels from its edges, where chroma and blocks blur) are taken over all
  pictures. The two ways are **identical** when every patch is within ±2 code
  values; a **level shift** when the greys stay grey (U and V at 128) in both but
  black and white differ in Y (full against limited range: 0/255 against
  16/235); a **matrix shift** when black, the greys and white agree and the
  saturated colours differ (BT.709 against BT.601); else **other**. The swatches
  the app shows are the measured YUV turned into RGB with BT.601 in the panel's
  range. Measured here with x264, both feeds give the same YUV to the code value;
  ffmpeg's `scale=out_range=full` gives a level shift (black 0 against 16) and
  `scale=out_color_matrix=bt709` a matrix shift (green's Y 173 against 145).
  *Show it on the panel* shows the test picture on a panel for 30 s, made every
  3 s the other way (a new ffmpeg each time, the panel stays open; a large A or
  B in a corner), written nowhere: the panel's loop asks for it on each frame,
  so its theme and feed come back when it ends, when it is stopped, or with the
  app (a marker beside `config.toml` tells the next start that the app ended
  during one; nothing else is left to undo).
- **The automatic choice** (`pixels = "auto"`, `devices/card_choice.py`). The
  card converts where the check found its conversion identical to the
  processor's, else the processor. The verdict is kept in `video-state.json` per
  encoder, range and ffmpeg, with ffmpeg's version line and the graphics
  driver's version (Windows: the display adapters' `DriverVersion` in the
  registry; Linux: NVIDIA's module and the kernel), for a week. Without one, the
  first start of a card's encoder runs the check once in the background: five
  frames each way, ten seconds at most per ffmpeg, one check at a time across
  the app's processes (a file beside `config.toml` names the one checking),
  never on the panel; the processor converts meanwhile, and the panel goes over
  to the card at its next frame once it is found right. A check that fails says
  nothing and is tried at the next start; the person's check (*Check again*)
  keeps its verdict the same way.
- Measured with Arena on the 12.3" (1920×720, 50 fps) and FFmpeg 8.1 on a
  4-core PC, the CPU time of Libre Panel and ffmpeg together per frame, before
  and after, in paired runs on a busy machine (its absolute numbers are about
  twice a quiet one's): with a card's encoder (the part up to the encoder)
  29.1 → 20.5 ms; with x264 40.9 → 35.5 ms; Intel's (up to its encoder) about
  2 ms less. ffmpeg's `transpose` turns four bytes a pixel for less than Pillow
  does (three for more), so ffmpeg turns the frames for every encoder.
- The stream above took about 170–300 KB/s: that is its own rate at the 2M
  cap, not a limit of the link. The link carries far more: 12–16 MB/s of JPEG
  stills were measured on the 9.2" (20 fps of 800 KB frames; sjcsoftware/
  turzx-linux, docs/protocol.md), and jijiechen/turing-monitor streams H.264
  to the 5.2" at 30 fps with 16 Mbit/s by default and 24 Mbit/s "for sharper
  output". What breaks playback is flooding the *queue* (sending faster than
  the player drains), which the flow control below prevents.
- Libre Panel scales `-maxrate` with the pixels and the frame rate: 2M is for
  1920×480 at 50 fps; the 12.3" (1920×720) starts at 3M. From there "auto"
  follows the panel up the steps 4 · 6 · 8 · 12 · 16 · 24 · 32 Mbit/s (never
  below its start: a panel that cannot keep up there cannot keep up with the
  pictures, as the 9.2" at 60 fps showed): a step down when,
  within 10 s, the sender waited for room in the queue five times (the decoder
  lags) or half the blocks (25 at least) took longer than 20 ms to send (the
  link lags; a healthy block takes about 1 ms), a step up after 60 s without
  that (a failed step is held off for 10 min, twice as long each time it fails
  again). Each step starts ffmpeg anew: x264 cannot change its rate on the way.
- A step does not freeze the picture: the new ffmpeg starts beside the old one, whose
  pictures go on to the panel until the new one's first (SPS, PPS, keyframe); one without a
  picture within 3 s is closed and the old one goes on (tried again after a minute).
- Measured on the 12.3" with a 9.2" beside it (2026-10, Settings → Panel →
  Video experiment, during a game): the 12.3" took every step without the
  sender waiting for room, nothing dropped, no faults, with caps up to 32M
  and streams up to 447 KB/s (AMD's encoder); crf 18 cost 190 KB/s at 21
  fps there and 244 KB/s at 48 fps on the 9.2"; the profiles **main and
  high show**, and so does **h264_amf** (AMD), alone and with high. So the
  defaults are crf 20, profile high on these two, and AMD's encoder where
  ffmpeg can start it. The 9.2" at 60 fps (70 told) **cannot**: 55 delivered,
  the sender waited 65 times. The hardware encoders h264_nvenc and h264_qsv
  are set to the same (no B-frames, one slice, the keyframe spacing, the
  cap) and remain an experiment until someone sees them show.
- Inside the 12.3" (board `QS400_123inch_V1.1`, 2025-12) sits an Allwinner
  T113-S3 with a custom top mark (eLQFP128, dual Cortex-A7, 128 MB DDR3 in the
  package, SPI flash, USB 2.0 OTG). Its decoder takes H.264 up to 1080p@60, so
  the bit rate a panel takes is bounded by its firmware's USB-to-decoder path,
  not by the decoder: what "auto" measures.

**verified**

### Blocks and flow control

- **One picture per 121 block.** The queue counts blocks: a keyframe cut
  into three blocks looks like a queue of three. Libre Panel cuts ffmpeg's
  output at NAL unit boundaries (a picture ends where the next one's first
  NAL unit begins).
- Block size ≤ 202752 (17 answers 0 = this default).
- Ask for the depth (122) every second block. Above 2, wait in 30 ms steps
  until it is at most 1 (give up after 1.5 s). The queue is a ring of five
  blocks that **silently overwrites** unread ones.
- **Report a higher rate than you deliver** (15): the player shows pictures
  strictly every 1000/fps ms and never catches up, so a delay once built up
  stays. 60 reported for 50 delivered keeps the queue at 0–1.

**verified**

### Stop

`123` (stop stream) → `15 = 30` → a last picture as PNG (102). 30 fps is the
panel's rate after power-on, and its own standby clip plays at the last rate
set: after 60 it stutters. With +5 V standby the panel keeps showing the last
picture while the PC is off. **verified**

### Overlays on top of running video

Each 102 holds the pipe for about 50 ms and delays the next 2–3 video
pictures. At most **2 overlays per second** keep 50 fps video smooth; at 5/s
the judder is visible, and from 10/s the video starves. **verified**

### A hung decoder

Rare (seen once in weeks): the panel takes each 121 block only after about
750 ms instead of about 1 ms, and the queue stands high without moving.
Detection, as in SPUR II: when the median time of the last 40 blocks exceeds
0.2 s, read the depth four times 150 ms apart; at least two answers, the
highest above 20 and all within 2 of each other, mean a hang (a high queue
that moves drains by itself). A new connection and a full start do not help,
not even a PC restart; restarting the panel (11) or replugging does.
**verified**

Recovery, as in SPUR II: send 11 (nothing else to the hung decoder, not even
123), wait until the panel has left the bus (up to 20 s) and is back (up to
90 s), give it 2 s more, then connect and start as after power-on. At most
three restarts in a row; the count starts again after 5 minutes without a
hang. When restarts do not help, or the panel does not come back, Libre
Panel asks to replug it. Plugins hear `panel-restarted` after
`panel-connected` when the panel came back from such a restart.

## The panel's own screens

The panel has two screens of its own that need no program on the PC: a JPEG at
power-on (`usr/data/boot.jpg`, else the factory logo `/media/logo.jpg` on another
partition, never written) and an H.264 clip it plays in a loop when the PC's USB
comes up, until a program streams (the name kept in its settings, `cfg+6`; the
vendor app's `usr/data/standby.h264`). SPUR II read this from the 8.8"'s firmware
and measured it on the 9.2". Libre Panel draws both in its own look for the
panel's size (`render/bootscreen.py`) and writes them on a click
(`devices/panel_files.py`), with the panel's themes paused first.

File commands share one layout: `[8:12]` the name's length (big endian),
`[12:16]` the file's length (40 only), `[16:]` the name, relative to the
application's folder (`usr/data/...`; `/usr/data` fails).

| Cmd | | |
|---|---|---|
| 40 | write a file | the file follows the packet in the same transfer, at most 1 MiB; written as named. Sent for `usr/data/boot.jpg` and `usr/data/standby.h264` alone, to the 9.2" and the 12.3", and never more than a panel is known to hold (see *What goes on a panel* below) |
| 42 | delete a file | taking `usr/data/boot.jpg` away brings the factory logo back |
| 98 | a file's size | little endian at `[8:12]`; checked after every write |
| 99 | list a folder | 20 pieces of 512 bytes from a 0x2800-byte buffer; it reads the name only at the first, so all 20 are fetched every time; it creates a missing folder |
| 113 | show a JPEG of the panel's | the boot logo, at once |
| 110 | play a clip | clears the framebuffer and keeps the name in the settings **in memory**; never with an empty name |
| 125 | save the settings | `[8:14]` brightness (0–102), start mode (2: the clip `cfg+6` names; 0: as the panel came), 0, rotation, 0, offline (0: dim and stop 10 s after the PC is gone); saved with the clip's name, into `usr/data/app.cfg` |

- **The boot logo** is drawn in the framebuffer's orientation (480×1920 on the
  9.2", turned as the frames are), JPEG quality 88. The JPEG path draws into a
  smaller area that leaves about 64 of 1920 px at both short ends as they were and
  does not clear the framebuffer, so nothing is near the ends; they fade to black.
- **The standby clip** is H.264 baseline with one slice a picture
  (`sliced-threads=0`), no B pictures, and a sequence (SPS, PPS and a keyframe)
  every 11 pictures (`keyint=11:repeat-headers=1`): the decoder takes at most 11
  pictures per sequence. 44 pictures at the 30 fps of a cold start loop in about
  1.5 s. A sequence start costs the panel about 30 ms and it refills its queue
  about once a second, so the clip moves only in brightness at fixed places.
- **Writing it:** 40 `usr/data/standby.h264`, 98 to check, 15 = 30, 110 with the
  name (it plays at once), 125 `[brightness, 2, 0, 0, 0, 0]`. The clip that was
  there cannot be read back first: the protocol has no file read.

### The room on a panel is not known

What one 9.2" (firmware `turzx_0001_0015`) told through the log of the app that
wrote to it (issue 52). Every number is from that log; what the panel does inside
is not known. On other panels, a 9.2" and a 12.3" among them, the same files were
written for months without any of this: what is different on that panel (its
firmware, its store, what else is in it) is not known either.

- **`usr/data` as that panel came** held `app.cfg` and `boot.sign`. `app.cfg` is the
  panel's own settings (what 125 saves); the start screen's files go beside it.
- **The room ran out.** After a clip of 149,040 bytes, a logo of 48,334 (deleted
  again) and four writes of a 701,830-byte clip within three weeks, a boot logo of
  126,833 bytes ended at 16,384: 40 was answered as ever, 98 said what was really
  there. Whether that panel holds about 0.7 MB in all, or more and does not give
  room back at once where a file is replaced or deleted, the log does not tell:
  two days later that logo was deleted (42; 98 said none), and the next logo
  written got no byte at all.
- **A panel without room takes no file**: for five days each 40 was answered
  within the time the transfer takes (30 to 50 ms for 700 KB; a write that happens
  takes 1.2 to 2.9 s) and nothing changed, whether the new file was larger or
  smaller than the one there. 98 then says the old file's size: Libre Panel read
  that as "the panel kept the file it had", and where the new clip was as long as
  the old one, as written.
- **0.9.1 forced a clip in**: a 40 with 706,912 bytes took 2.4 s and ended at
  704,512, which is the old clip's 701,830 bytes rounded up to whole blocks of
  4 KiB; 0.9.1 took that away (42) and wrote again, and 98 said 706,912. Then 15,
  110 and 125: the settings saved with that clip as the start. The panel showed
  frames for ten more seconds, until Windows shut down. Since its next start it is
  on the USB for about 2 s and gone for 1, without end, with no picture, on a
  charger as well.
- **Why it restarts is not known.** Two things fit. Its settings went into a store
  with no room, and it fails on them. Or the clip's end is not on the panel as it
  was sent (98 says the size the panel has for the file, not what it can read),
  and the panel fails where its player gets there: the clip is 66 pictures, 2.2 s
  at the panel's 30 a second, about as long as the panel stays on the USB.
- **It answered the app nothing after that.** In the five hours after, the app
  opened the panel thirteen times while it came and went, and sent it the sync
  (10): no answer came to any (the transfer failed five times as a stalled
  endpoint, six times as a device that was gone, twice without an answer in
  time), and of the app's looks every five seconds in between, not one ended with
  a panel in use. So the panel was not seen to take a command in its two seconds.
  Whether it takes none, or took none at the moments the app tried, that log does
  not tell. The app sent the sync once at each opening and waited two seconds: on
  a sound panel a sync of about the first third of a second is lost
  ([below](#a-92-as-it-starts)), so one or the other of the thirteen may have been
  lost that way; all of them would not be on a panel that answers through the
  rest of its two seconds.
- **42 does delete** on that panel: after it 98 says none for a boot logo that was
  there, twice in that log. It was never sent for a clip the panel was playing.

So: how much room a panel has is not known, is not the same on every panel and
cannot be asked (100 is the card's); the settings are in the same place; a write
that ends early leaves a file cut off; and a size that matches proves nothing
where the old file was as long, and may not prove that the file can be read.
0.9.2 and 0.9.3 wrote no file to a panel. Libre Panel never deleted a file to
write it again before 0.9.1 and does not after it.

### What goes on a panel

Since 0.9.4 (`devices/panel_files.py`, `rust/app/src/start_screen.rs`):

- **Where the files had grown.** Until 0.7.8 a 9.2" got a clip of about 149,000
  bytes and a logo of 48,000 (x264 crf 23, JPEG quality 88). 0.7.9 made them "as
  fine as fits in 1 MiB", which is the most 40 takes and not a panel's room: crf
  20, and 17 where the clip stayed under 40 % of 1 MiB; quality 95 with every
  pixel's colour. For a look on a 9.2" that came to 615,000 to 875,000 bytes
  (Circuit: a clip of 715,000 at crf 17 where crf 20 gives 395,000, with 1.4 dB
  between them at 52; Levels: 1,369,000), on a 12.3" to 643,000 to 1,150,000
  (Levels: 1,534,000). The 9.2" of issue 52 had room for about 718,000.
- **The room a panel's files get**: on the 9.2" 360,000 bytes for the clip and
  120,000 for the logo, two thirds of what filled one; on the 12.3" 500,000 and
  150,000, four fifths of the 817,587 bytes one has held for weeks (where its
  room ends is not known). Since 0.9.5 the 8.8" gets the 9.2"'s: its maker sells
  the two as one, with the same framebuffer. The clip is made at crf 20 and,
  where that is too much, a step coarser at a time up to 26, then a quarter of a
  step finer again where that still fits (the three quarters made at once); the
  logo at the finest whole quality from 95 down to 85 that fits (0.9.4 tried 95,
  92, 90, 88 and 85 only), every pixel with its own colour (a colour for four was
  the worse trade: on Circuit's traces 38.7 dB where the same bytes gave 42.1). A
  look that would have to be coarser is not made: Levels, on both. What the
  others come to (ffmpeg 6.1 on Linux; another build's clips are a per cent or two
  apart, and find their own quarter), and how close they stay to what was drawn
  (PSNR; the clip against the lossless clip of its pictures):

  | Look | 9.2": clip | logo | together | 12.3": clip | logo | together |
  |---|---|---|---|---|---|---|
  | Libre Panel | 352,645 (51.85 dB, crf 20; 0.9.0: 606,261, 53.4) | 108,620 (47.80, quality 94; 127,210, 48.3) | 461,265 | 477,724 (52.15, crf 20; the same) | 140,346 (48.35, 94; 165,169, 48.7) | 618,070 |
  | Aurora | 349,368 (49.95, crf 24.25; 652,208, 51.1) | 117,959 (49.91, 95; the same) | 467,327 | 497,227 (50.38, crf 24.5; 988,183, 51.5) | 137,422 (49.88, 94; 161,725, 50.0) | 634,649 |
  | Pulse | 359,725 (51.58, crf 21.75; 464,168, 52.3) | 116,362 (46.86, 93; 150,737, 47.8) | 476,087 | 491,809 (52.36, crf 21; 604,690, 52.7) | 141,623 (47.68, 93; 187,968, 48.6) | 633,432 |
  | Circuit | 355,625 (51.18, crf 20.5; 715,341, 52.8) | 113,040 (46.08, 92; 159,776, 47.8) | 468,665 | 492,450 (50.98, crf 21; 602,822, 51.4) | 149,517 (45.88, 91; 223,833, 47.9) | 641,967 |
  | Night | 348,994 (52.55, crf 22.75; 574,056, 53.3) | 104,757 (48.66, 95; the same) | 453,751 | 497,619 (52.60, crf 23.25; 897,818, 53.5) | 137,679 (49.10, 95; the same) | 635,298 |
  | Dark | 45,000 (70.30, crf 20; 51,771, 72.0) | 14,289 (65.15, 95; the same) | 59,289 | 73,830 (69.02, crf 20; 85,817, 70.5) | 21,952 (64.57, 95; the same) | 95,782 |

  For a panel mounted on end the pictures are others (drawn upright for 480×1920
  and 720×1920), and so are the files:

  | Look | 9.2": clip | logo | together | 12.3": clip | logo | together |
  |---|---|---|---|---|---|---|
  | Libre Panel | 339,014 (51.97 dB, crf 20; 0.9.0: 584,330, 53.5) | 101,824 (47.97, quality 94; 120,502, 48.4) | 440,838 | 470,238 (51.97, crf 20; the same) | 135,973 (48.35, 94; 160,679, 48.7) | 606,211 |
  | Aurora | 347,820 (47.18, crf 25.25; 796,787, 49.6) | 113,670 (49.54, 94; 131,336, 49.7) | 461,490 | 486,044 (47.75, crf 25.25; 796,589, 49.2) | 141,154 (49.68, 93; 185,952, 50.0) | 627,198 |
  | Pulse | 349,677 (50.35, crf 22.75; 513,382, 51.7) | 111,521 (45.72, 92; 160,629, 47.2) | 461,198 | 489,997 (51.72, crf 21.5; 652,522, 52.3) | 146,703 (47.23, 93; 194,549, 48.2) | 636,700 |
  | Circuit | 354,700 (51.14, crf 20.5; 711,836, 52.7) | 113,393 (46.18, 92; 160,503, 47.9) | 468,093 | 481,563 (51.05, crf 21; 595,882, 51.5) | 149,015 (45.89, 91; 222,915, 48.0) | 630,578 |
  | Night | 352,784 (53.26, crf 21.75; 575,996, 53.7) | 81,343 (49.02, 95; the same) | 434,127 | 490,326 (53.18, crf 22.25; 873,977, 53.8) | 115,184 (49.23, 95; the same) | 605,510 |
  | Dark | 44,517 (70.16, crf 20; 50,283, 71.8) | 14,310 (65.13, 95; the same) | 58,827 | 73,428 (68.84, crf 20; 84,481, 70.3) | 22,047 (64.60, 95; the same) | 95,475 |

  Turned round for a panel that is mounted upside down, the files are made the
  same way: the logos come out within 0.12 % of these, the clips at a step of
  their own and within the same room.

  The clips that are not at crf 20 now use 96 to 99.9 % of their room; with
  whole steps (0.9.4) it was 85 to 98.5 %. Aurora on end is the clip furthest
  from 0.9.0's (797,000 bytes on either panel, at crf 20 on the 9.2" and 22 on
  the 12.3"): 2.4 dB on the 9.2" and 1.45 on the 12.3", where every other clip,
  lying or on end, is within 1.7; its finest rays are a little softer. 0.9.4 made
  it at crf 26, the coarsest step (46.8 and 47.4 dB). The logos are within 2.1 dB
  of 0.9.0's (Circuit's on the 12.3" at 91 the furthest), at 45.7 dB or better.
  Upright, a look comes to 434,127 to 476,087 bytes on the 9.2" and 605,510 to
  641,967 on the 12.3" (Dark: 58,827 and 59,289; 95,475 and 95,782).

- **Before 40**, 98 for the file and for the other one. A file larger than the
  one it replaces is not sent where both together would be more than that room;
  one that is no larger always goes (a panel that holds a 700 KB clip of 0.9.0
  gets its clip anew, smaller, and then has room for a logo).
- **After 40**, 98 again. The size sent: written. The size of before: the panel
  did not take the file, which is said, and nothing more is sent (no 113, no
  110, no 125). Where the file of before had the new one's size, the time 40
  took tells: under a second for 4 MB is no write (that 9.2" answered 30 to
  50 ms after 700 KB it did not take, and after 1.2 to 2.9 s when it wrote them;
  below 32 KiB, which on these two panels only Dark's boot logos are, the
  transfer's own time says nothing, and the file is taken as written).
- **Any other size: the file ended early**, and the panel is full. 42 takes it
  off again at once, 98 checks that it is gone, and what the panel took is
  said. Nothing is written a second time. This is the one command sent to a
  panel in that state, and it cannot be tried on a sound panel. The panel of
  issue 52 took 42 in that state twice, by its log: for the boot logo that had
  ended at 16 KiB (*Factory logo again*, after which it ran for three more
  days), and for the clip 0.9.1 took away (after which it took the next file
  and showed frames until its next start). What ended it came after: the larger
  clip written into the room the delete had made, and the settings saved.
- **The 9.2", the 12.3" and the 8.8"** are written to (the 8.8" since 0.9.5,
  with the 9.2"'s room; the first write to one is an owner's). Another model of
  the family is written to only on its owner's word, given for each write after
  the app has said what happened to issue 52's panel (`untested: true` with the
  write; or `LIBRE_PANEL_PANEL_WRITES=at-my-own-risk` in the environment): its
  files are then made for the 9.2"'s room, by the pixels, and never for more, and
  sent and checked with the same commands. How it went is asked for in the
  [start screen issue form](https://github.com/syntensa/libre-panel/issues/new?template=start_screen.yml);
  a model goes on the list of those written to without the question only after
  such a report from a panel of it.
- **Run on two sound panels** before 0.9.4 (0.9.4rc1 on Windows, 2026-11-16): a
  9.2" with firmware `turzx_0001_0015`, mounted on end, and a 12.3" with
  `turzx_0001_0026`. Every 40, in the order sent, with the size 98 said before
  and how long the panel took to answer it; after each, 98 said the size sent:

  | Panel | File | Bytes sent | The panel had | 40 answered after |
  |---|---|---|---|---|
  | 9.2" | clip (Circuit) | 319,122 | 51,296 | 568 ms |
  | 9.2" | logo (Circuit) | 113,393 | 14,287 | 209 ms |
  | 9.2" | clip (Dark) | 44,931 | 319,122 | 1,764 ms |
  | 9.2" | clip (Dark) | 44,931 | 44,931 | 48 ms |
  | 9.2" | logo (Circuit) | 113,393 | none | 195 ms |
  | 9.2" | clip (Circuit) | 319,122 | 44,931 | 564 ms |
  | 12.3" | clip (Circuit) | 485,481 | 593,754 | 1,306 ms |
  | 12.3" | clip (Circuit) | 485,481 | 485,481 | 1,115 ms |
  | 12.3" | logo (Circuit) | 141,864 | 223,833 | 321 ms |
  | 12.3" | logo (Circuit) | 141,864 | none | 157 ms |

  Between the logos, 42 took `usr/data/boot.jpg` off on both (98 then said none,
  and the factory logo showed at the next start): the first 42 seen on a 12.3".
  Both panels started with what was written after they had been unplugged.
  Where 98 proves the write (the panel had another size, or none), it ran at
  0.37 to 0.90 MB a second, and once at 0.03: Dark's clip of 44,931 bytes over
  Circuit's of 319,122 took 1.8 s. The two over a file of their own size, 48 ms
  and 1.1 s (0.94 and 0.44 MB a second), are 4.3 and 9 times slower than the
  4 MB a second from which an answer counts as no write; the panel of issue 52
  answered 30 to 50 ms after 700 KB it did not take, 14 MB a second and more.
  The logos are the bytes Linux makes; the clips, from the Windows download's
  ffmpeg, are 0.5 to 1.4 % from the tables above. Not seen, since no sound panel
  shows it: a file that ends early, and one not taken.

For a panel in that state, `libre-panel start-screen remove-clip --wait` acts the
moment the panel is on the USB: 10 each time the panel comes, until it is answered
(sent again every 300 ms while it is not, and 100 ms after one that the panel's
port sent back: an endpoint can take a packet or two by itself, so whether the
panel goes on taking them tells whether anything on it reads what is sent), then
111 (the clip it may be playing stopped, as the app does at each of its starts),
42 for the clip and 98 to see that it is off: see
[configuration](../CONFIGURATION.md#the-panels-start-screen). The settings are
saved with start mode 0 (125) only once the clip is off, and only with
`default-start` or where `app.cfg` is there and empty. It waits for the panel
until its time is over, however often the panel comes without an answer. What
comes back and is no answer (a port's echo) is told once each time the panel
comes and counts as none; a step is sent only after the sync's answer.

The chance was small: that panel answered the app nothing after it began to
restart. What the command has over the app's tries is that it is there the moment
the panel comes (the app looked every five seconds, and started its engine
first), that it sends the sync again until it is answered, that it goes on for as
long as it is given, and that it says to the millisecond how far each coming
gets: whether the panel can be opened, whether its port takes what is sent,
whether the port sends it back unread, whether the panel answers.

**What it found on that panel** (0.9.3's `remove-clip --wait`, run by its owner on
Windows, 2026-11-16): in its ten minutes the panel came 81 times, about every
7.5 s, and stayed 3.9 to 7.8 s, mostly 5.8. Each time it could be opened (WinUSB),
and each time its port took nothing: the sync waited until the panel left, and
the transfer ended as a stalled endpoint or as a packet not taken, in turn. A
sound 9.2" with that firmware takes the first packet 0.32 s after it comes
([below](#a-92-as-it-starts)). So the panel's program never gets as far as reading
its USB port before the panel starts again, and nothing a PC sends reaches it:
not this command, not the app, and as far as is known not the vendor's app
either, which talk to it the same way. `default-start` would meet the same port.
What may still bring such a panel back is its maker's: a way to put its software
back that is not known here. Where a panel comes and goes and its port takes
nothing in a dozen comings, the command's last line says so (`its port took
nothing that was sent`), and that is the answer: nothing more can be done from
the PC.

### A 9.2" as it starts

Measured twice on a sound 9.2" with that firmware (`turzx_0001_0015`), plugged
in on Windows while `libre-panel start-screen --wait` watched the bus: with
0.9.2, which sent the sync again 300 ms after one that came back, and with 0.9.3,
which sends it 100 ms after. The times are since the panel was on the bus.

| | 0.9.2 | 0.9.3 |
|---|---|---|
| listed with the WinUSB driver, can be opened | 0 ms | 0 ms |
| its port takes nothing: a packet sent waits | until 308 ms | until 318 ms |
| the port takes the sync and sends it back, in pieces that are no reply | at 309 ms, eleven | at 319 ms, twelve (82 bytes) |
| the next sync is sent | at 611 ms | at about 420 ms |
| and answered: `0A C8`, the request's time stamp, `turzx_0001_0015` | at 656 ms | at 422 ms |
| the commands after it are answered | within 2 to 3 ms | within 2 ms; 98 of `app.cfg` and `boot.sign` after 33 and 66 ms, 99 after 30 |
| 112: the panel's own clip is playing (`[8]` = 1), and 98 and 99 are answered while it plays | at 670 ms | at 527 ms |
| still there, still answering | at 20.7 s | at 20.6 s |

What comes back is control bytes written out (`^X`: `5E 5A`), `^R` with CR LF
and the line again, BS space BS. A terminal's line mode does that with what it
gets: the port is open, and the panel's program has not set it up yet. That sync
is lost.

So what a sound panel is sent in about its first third of a second is lost, and
a sync sent 100 ms after the one that came back was answered at once; when the
panel would have answered between the two is not known. (0.9.3's notes said "from
0.66 s on": that was when 0.9.2's second sync was answered, sent only 300 ms
after the first came back.) What an app sees of a panel that restarts depends on
when it sends: the sync goes first at every coming and again until it is
answered.

What two sound panels held then, by 98 and 99: the 9.2" `app.cfg` (132 bytes),
`boot.sign` (0), `boot.jpg` (14,287) and `standby.h264` (51,296), the Dark look;
a 12.3" (firmware `turzx_0001_0026`) `app.cfg` (136), `boot.sign` (0), `boot.jpg`
(223,833) and `standby.h264` (593,754), Circuit.

### Which panel, which maker: what is known where

A command is known for the panel it was seen on, not for its neighbours.

- **The vendor id `1CBE` is not one maker's.** Lian Li's screens carry it too
  (`A088`, `A092`, `A034`, `A068`) and speak a protocol of their own; nothing on
  this page is theirs. This page is the TURZX family's: the product ids at its top.
- **Within the family, models and firmwares may differ.** The file commands (40,
  42, 98, 99, 113) and 125 are SPUR II's reading of the 8.8"'s firmware; they were
  seen to work on the 9.2" (SPUR II's own, and the one of issue 52 with firmware
  `turzx_0001_0015`, by its log: 40 wrote files, 98 said their sizes, 99 listed the
  folder, 42 deleted, 125 with start mode 2 was taken). On a 12.3" the app's
  writing (99, 40, 98, 113, 110, 125 with start mode 2) has been in use for months
  as well, and 42 took its boot logo off (firmware `turzx_0001_0026`, 0.9.4rc1; 98
  then said none). On the family's other models (1.6" to 8.8", the
  Jonsbo TF3's pump screen, another maker's) they are untried. And two panels of
  one model need not be alike: the 9.2" of issue 52 ran out of room where others
  never did. `libre-panel start-screen` sends its steps to the 9.2" alone, also
  where another panel of the family is on the same PC: that one is told, not
  touched (`--model` names the panel a look is at).
- **Other projects describe other panels.** turing-smart-screen-python
  (`lcd_comm_turing_usb.py`), phstudy/turing-smart-screen-cli and
  jijiechen/turing-monitor (a 5.2" that names itself `turzx_0001_0015` too) upload
  a file as the vendor app does: 38 opens it, 39 sends it in pieces. They call 40
  with a path and no length "delete file" and 98 "play file", send 13 and 41 bare
  before a video, and use 42 nowhere; in their 125 the start mode is 0 for the
  panel's default, 1 for a picture, 2 for a video. None of that was checked on a
  9.2". The one thing Libre Panel takes from there is start mode 0 in
  `libre-panel start-screen default-start`, and it says so.

