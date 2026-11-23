"""The colour of a cover (render/cover.py): its strongest large colour, none of greys or darkness,
each looked at once, and none kept alive for a track that is gone."""

from PIL import Image

from libre_panel.render import cover

# -- which card ------------------------------------------------------------------------------


# -- a change --------------------------------------------------------------------------------


# -- other ways from one card to the next ----------------------------------------------------


# -- cards in turn ----------------------------------------------------------------------------


# -- a new track ------------------------------------------------------------------------------


# -- the frame -------------------------------------------------------------------------------


# -- cutting a piece to rows -----------------------------------------------------------------


# -- the colour of a cover -------------------------------------------------------------------


def picture(colour, size=(64, 64)):
    return Image.new("RGB", size, colour)


def test_a_cover_carries_its_strongest_large_colour():
    red = cover.dominant(picture((200, 30, 40)))
    assert red is not None and red[0] > red[1] and red[0] > red[2] and max(red) >= cover.BRIGHT
    two = Image.new("RGB", (64, 64), (90, 90, 95))  # a grey ground ...
    two.paste((20, 200, 90), (0, 0, 24, 64))  # ... and a green stripe that is a third of it
    green = cover.dominant(two)
    assert green is not None and green[1] > green[0] and green[1] > green[2]


def test_a_cover_of_greys_or_darkness_has_no_colour_and_a_dark_one_is_brightened():
    assert cover.dominant(picture((128, 128, 128))) is None
    assert cover.dominant(picture((20, 10, 10))) is None
    assert cover.dominant(None) is None
    dim = cover.dominant(picture((70, 20, 20)))
    assert dim is not None and max(dim) >= cover.BRIGHT
    assert dim[0] > dim[1]  # (still red)


def test_a_cover_is_looked_at_once():
    image = picture((10, 160, 200))
    first = cover.dominant(image)
    assert cover.dominant(image) is first
    for i in range(cover.KEPT + 2):
        cover.dominant(picture((200, 10 + i, 10)))
    assert cover.dominant(image) == first  # (looked at again, the same answer)
    assert len(cover._kept) <= cover.KEPT


def test_the_covers_of_tracks_gone_are_not_kept_alive_for_their_colour():
    """A cover is up to a megabyte: the colour it carries is kept, the picture is not."""
    import gc
    import weakref

    image = picture((10, 160, 200))
    found = cover.dominant(image)
    gone = weakref.ref(image)
    del image
    gc.collect()
    assert gone() is None  # (nothing here held it)
    other = picture((10, 160, 200))
    assert cover.dominant(other) == found  # (an id that comes back is looked at anew)


# -- the slot in a theme ---------------------------------------------------------------------
