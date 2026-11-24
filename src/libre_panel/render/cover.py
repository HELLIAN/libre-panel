"""The colour a cover carries: what a card's frame takes on while a track plays; and its
colours, the light of a light field in a cover's colours takes (rust/render/src/field.rs's TINTED).

Not the average (every cover averages to a brownish grey): the picture is reduced to a few
colours and the one that joins how much of it there is with how colourful it is wins, so a
small strong area carries a cover more than a large grey ground. Too dark and too pale
candidates are left out; a cover of nothing but greys has none. A colour that is too dark to
read as a thin line on a dark panel is brightened until it is not. Its colours: the first
:data:`LIGHTS` candidates in that order, each as bright as a colour of it can be.
"""

from __future__ import annotations

import threading
import weakref
from collections import OrderedDict

from PIL import Image

SIDE = 48  # the cover is looked at this small
COLOURS = 8
DARKEST = 60  # a candidate whose brightest channel is below this carries nothing
GREYEST = 0.18  # ... and one less colourful than this is grey
BRIGHT = 180  # the winner is brightened to this, as a line on dark ground
LIGHTS = 3  # the colours of a cover a light takes
KEPT = 8

RGB = tuple[int, int, int]

# by picture: its candidates (_found). The picture itself is not held (a cover is up to a
# megabyte, and the eight last were kept alive here after their tracks): an entry whose
# picture is gone is found dead and made anew, so an id that comes back is never taken for it.
_kept: OrderedDict[int, tuple[weakref.ref, list[RGB]]] = OrderedDict()
_lock = threading.Lock()


def dominant(image: Image.Image | None) -> RGB | None:
    """The colour that carries ``image``, or None (no picture, or only greys). Worked out
    once per picture: a track's cover is the same object for as long as it plays."""
    found = _candidates(image)
    return _brightened(found[0], BRIGHT) if found else None


def colours(image: Image.Image | None) -> list[RGB]:
    """The colours of ``image`` a light takes: its first :data:`LIGHTS` candidates, each as
    bright as a colour of it can be; none for no picture or only greys."""
    out = []
    for colour in _candidates(image)[:LIGHTS]:
        top = max(colour)  # (whole numbers: the brightest channel becomes 255 itself)
        out.append((colour[0] * 255 // top, colour[1] * 255 // top, colour[2] * 255 // top))
    return out


def _brightened(colour: RGB, to: int) -> RGB:
    """``colour`` brightened until its brightest channel is about ``to``, where it is below."""
    top = max(colour)
    if top >= to:
        return colour
    k = to / top
    return (min(255, int(colour[0] * k)), min(255, int(colour[1] * k)),
            min(255, int(colour[2] * k)))  # fmt: skip


def _candidates(image: Image.Image | None) -> list[RGB]:
    if image is None:
        return []
    with _lock:
        hit = _kept.get(id(image))
        if hit is not None and hit[0]() is image:
            _kept.move_to_end(id(image))
            return hit[1]
    found = _find(image)
    with _lock:
        _kept[id(image)] = (weakref.ref(image), found)
        while len(_kept) > KEPT:
            _kept.popitem(last=False)
    return found


def _find(image: Image.Image) -> list[RGB]:
    """The colours that can carry ``image``, the one that carries it most first: each by how
    much of it there is and how colourful it is (of equals, the one met first)."""
    try:
        small = image.convert("RGB").resize((SIDE, SIDE), Image.Resampling.BILINEAR)
        few = small.quantize(colors=COLOURS, method=Image.Quantize.MEDIANCUT).convert("RGB")
        scored: list[tuple[float, RGB]] = []
        for count, (r, g, b) in few.getcolors(SIDE * SIDE) or []:
            top = max(r, g, b)
            if top < DARKEST:
                continue
            colourful = (top - min(r, g, b)) / max(1, top)
            if colourful < GREYEST:
                continue
            scored.append(((count / (SIDE * SIDE)) ** 0.5 * colourful, (r, g, b)))
    except (OSError, ValueError):  # a picture Pillow cannot read
        return []
    return [colour for _, colour in sorted(scored, key=lambda s: -s[0])]
