import pytest

from libre_panel.devices.models import MODELS, get_model, model_for_size, models_for_usb


def test_ids_are_unique():
    ids = [m.id for m in MODELS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "model, landscape",
    [
        ("turing-3.5", (480, 320)),
        ("turing-5", (800, 480)),
        ("turing-8.8-usb", (1920, 480)),
        ("turing-9.2-usb", (1920, 480)),  # 480, not the 462 of the reference library
        ("turing-12.3-usb", (1920, 720)),
        ("turing-2.1", (480, 480)),
    ],
)
def test_sizes(model, landscape):
    panel = get_model(model)
    assert panel.size("landscape") == landscape
    assert panel.size("portrait") == landscape[::-1]


def test_usb_lookup():
    assert [m.id for m in models_for_usb(0x1CBE, 0x0092)] == ["turing-9.2-usb"]
    shared = {m.id for m in models_for_usb(0x1A86, 0x5722)}
    assert {"turing-3.5", "xuanfang-3.5"} <= shared
    assert [m.id for m in models_for_usb(0x1A86, 0x5722, "2017-2-25")] == ["xuanfang-3.5"]
    assert models_for_usb(None, None) == []


def test_driver_status():
    assert {m.driver for m in MODELS if m.protocol == "usb-turing"} <= {"unverified", "supported"}
    assert get_model("turing-3.5").driver == "supported"  # rev. A, an owner's report
    assert get_model("usbpcmonitor-7").driver == "unverified"  # rev. A too, not seen yet
    assert get_model("turing-5").driver == "unverified"  # rev. C
    assert get_model("turing-9.2-usb").driver == "supported"  # stills and video, on the panel
    assert get_model("turing-8.8-usb").driver == "supported"  # an owner's report
    assert get_model("turing-12.3-usb").driver == "supported"  # the maintainer's
    assert get_model("turing-8.8").driver == "supported"  # the older one, a serial rev. C
    assert {m.driver for m in MODELS} <= {"unverified", "supported"}  # every family has one


def test_model_for_size():
    assert model_for_size(320, 480).id == "turing-3.5"


# -- Thermalright ---------------------------------------------------------------------------------


def test_thermalrights_trofeo_visions():
    assert {m.id for m in models_for_usb(0x0416, 0x5408)} == {
        "thermalright-trofeo-9.16",
        "thermalright-trofeo-11.3",
    }
    assert [m.id for m in models_for_usb(0x0416, 0x5302)] == ["thermalright-trofeo-6.86"]
    assert get_model("thermalright-trofeo-9.16").size("landscape") == (1920, 462)
    assert get_model("thermalright-trofeo-6.86").driver == "unverified"


# -- Thermaltake ----------------------------------------------------------------------------------


def test_thermaltakes_6_inch_lcd():
    assert models_for_usb(0x264A, 0x2347) == [get_model("thermaltake-lcd-6")]
    assert get_model("thermaltake-lcd-6").size("landscape") == (1480, 720)


# -- udisp displays -------------------------------------------------------------------------------


def test_waveshares_2_8_inch_udisp_display():
    model = get_model("waveshare-2.8-udisp")
    assert models_for_usb(0x303A, 0x2986) == [model]
    assert model.size("landscape") == (480, 480) and model.shape == "round"
    # verified on an owner's desk (#30, #33): no "not yet seen", what it needs and its backlight
    assert model.driver == "supported" and "udisp" in model.limits
    assert not model.limits.startswith("Not yet seen") and "backlight" in model.limits
