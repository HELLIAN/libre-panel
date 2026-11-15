"""The panel's start screen in Settings in a real browser; skipped without Playwright or
Chromium."""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import app_url, browser_page, choose_theme  # noqa: E402, F401

PANEL_SWITCH = "Which panel Start and Status show and Design edits"
WRITES_OFF = (
    'Writing to the panel is switched off in this version. One 9.2" ran out of room for its '
    "start screen and has kept restarting since; on other panels the same files were written "
    "without trouble. Writing comes back when Libre Panel can tell how much fits on a panel."
)


def test_the_start_screen_does_not_write_in_this_version(app_url, isolated_home):  # noqa: F811
    """Issue 52: until it is known what made a 9.2" keep restarting, the card says that writing
    is switched off and its two "Put on the panel" stay off; the pictures show and download,
    and the factory logo can still come back."""
    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-9.2-usb"\nrotate = 180\n', encoding="utf-8"
    )
    asked = []

    def answer(route, request):
        if request.method != "POST":
            return route.continue_()
        body = json.loads(request.post_data)
        asked.append(body)
        reply = {"model": body["model"], "boot_logo": 0, "paused": False}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))

    with browser_page(app_url, "settings/panel", routes={"**/api/start-screen": answer}) as page:
        card = page.locator("#start-screen")
        expect(card.locator(".note").first).to_have_text(WRITES_OFF)
        expect(card.locator(".note")).to_have_count(1)  # (nothing about writing turned round)
        expect(card.locator(".boot-picture img")).to_have_count(2)
        for n in range(2):
            expect(card.get_by_role("button", name="Put on the panel").nth(n)).to_be_disabled()
        expect(card.get_by_role("link", name="JPEG")).to_be_visible()
        expect(card.get_by_role("link", name="H.264")).to_be_visible()
        card.get_by_role("button", name="Factory logo again").click()
        page.get_by_role("button", name="Factory logo again").last.click()
        expect(page.locator(".toast")).to_contain_text("The factory logo shows again")
        assert [body["action"] for body in asked] == ["remove-logo"]


@pytest.mark.usefixtures("panel_writes")
def test_the_start_screen_asks_before_it_writes_and_shows_both_pictures(app_url, isolated_home):  # noqa: F811
    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-9.2-usb"\n', encoding="utf-8"
    )
    asked = []

    def answer(route, request):
        if request.method != "POST":
            return route.continue_()
        body = json.loads(request.post_data)
        asked.append(body)
        reply = {"model": body["model"], "boot_logo": 47000, "paused": False}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))

    with browser_page(app_url, "settings/panel", routes={"**/api/start-screen": answer}) as page:
        card = page.locator("#start-screen")
        expect(card.locator(".boot-picture img")).to_have_count(2)
        expect(card.locator("select")).to_have_value("turing-9.2-usb")  # the panel at hand
        pictures = card.locator(".boot-picture img").evaluate_all("i => i.map((x) => x.src)")
        assert "what=logo" in pictures[0] and "what=clip" in pictures[1]
        page.wait_for_function(
            "() => [...document.querySelectorAll('#start-screen .boot-picture img')]"
            ".every((i) => i.complete && i.naturalWidth > 0)"
        )
        logo = card.get_by_role("button", name="Put on the panel").first
        logo.click()
        page.get_by_role("button", name="Cancel").click()  # asked, and nothing written
        assert asked == []
        logo.click()
        page.get_by_role("button", name="Write it").click()
        expect(page.locator(".toast")).to_contain_text("The boot logo is on the panel (46 KB)")
        assert asked == [{"action": "logo", "model": "turing-9.2-usb", "panel": 0,
                          "design": "libre-panel"}]  # fmt: skip
        card.locator("select").select_option("turing-4.6-usb")
        expect(card.locator(".note")).to_contain_text('Measured on the 9.2"')
        assert "turing-4.6-usb" in card.locator(".boot-picture img").first.get_attribute("src")


@pytest.mark.usefixtures("panel_writes")
def test_the_start_screen_is_the_one_of_the_panel_chosen_at_the_top(app_url, isolated_home):  # noqa: F811
    """With a 9.2" beside a 12.3": Panel 2 in the header shows and writes panel 2's own logo
    and clip, in its size, and says which panel it is."""
    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-12.3-usb"\n'
        '[[panels]]\nmodel = "turing-9.2-usb"\n', encoding="utf-8"
    )  # fmt: skip
    asked = []

    def answer(route, request):
        if request.method != "POST":
            return route.continue_()
        body = json.loads(request.post_data)
        asked.append(body)
        reply = {"model": body["model"], "boot_logo": 47000, "paused": False}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))

    with browser_page(app_url, "settings/panel", routes={"**/api/start-screen": answer}) as page:
        card = page.locator("#start-screen")
        expect(card.locator("select")).to_have_value("turing-12.3-usb")  # panel 1's
        switch = page.get_by_role("radiogroup", name=PANEL_SWITCH)
        switch.get_by_role("radio", name="Panel 2").click()
        expect(card.locator("select")).to_have_value("turing-9.2-usb")  # panel 2's
        expect(card).to_contain_text("Start screen · Panel 2")
        expect(card.locator(".note").first).to_contain_text("For the panel chosen at the top")
        assert "panel=1" in card.locator(".boot-picture img").first.get_attribute("src")
        card.get_by_role("button", name="Put on the panel").first.click()
        page.get_by_role("button", name="Write it").click()
        expect(page.locator(".toast")).to_contain_text("The boot logo is on the panel")
        assert asked == [{"action": "logo", "model": "turing-9.2-usb", "panel": 1,
                          "design": "libre-panel"}]  # fmt: skip


@pytest.mark.usefixtures("panel_writes")
def test_a_look_is_chosen_for_both_screens(app_url, isolated_home):  # noqa: F811
    """The looks as small boot logos: the one chosen is what both pictures show, what the clip's
    words say it does, what is downloaded and what is written."""
    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-12.3-usb"\n', encoding="utf-8"
    )
    asked = []

    def answer(route, request):
        if request.method != "POST":
            return route.continue_()
        body = json.loads(request.post_data)
        asked.append(body)
        reply = {"model": body["model"], "standby_clip": 640000, "paused": False}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))

    with browser_page(app_url, "settings/panel", routes={"**/api/start-screen": answer}) as page:
        card = page.locator("#start-screen")
        looks = card.get_by_role("radiogroup", name="Look of the start screen")
        expect(looks.get_by_role("radio")).to_have_count(7)
        expect(looks.get_by_role("radio", name="Libre Panel")).to_have_attribute(
            "aria-checked", "true"
        )
        expect(card).to_contain_text("a light that runs out from the middle")
        looks.get_by_role("radio", name="Night").click()
        expect(looks.get_by_role("radio", name="Night")).to_have_attribute("aria-checked", "true")
        expect(card).to_contain_text("stars that twinkle under the moon")
        pictures = card.locator(".boot-picture img").evaluate_all("i => i.map((x) => x.src)")
        assert all("design=night" in src for src in pictures)
        assert "design=night" in card.get_by_role("link", name="H.264").get_attribute("href")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#start-screen .boot-look-tile img')]"
            ".every((i) => i.complete && i.naturalWidth > 0)"
        )
        card.get_by_role("button", name="Put on the panel").nth(1).click()
        page.get_by_role("button", name="Write it").click()
        expect(page.locator(".toast")).to_contain_text("The standby clip is on the panel")
        assert asked == [{"action": "clip", "model": "turing-12.3-usb", "panel": 0,
                          "design": "night"}]  # fmt: skip
