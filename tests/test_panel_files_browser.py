"""The panel's start screen in Settings in a real browser; skipped without Playwright or
Chromium."""

import json

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import expect  # noqa: E402
from test_app_browser import app_url, browser_page, choose_theme  # noqa: E402, F401

PANEL_SWITCH = "Which panel Start and Status show and Design edits"
UNTRIED = (
    "No one has tried start screens on this panel yet, and how much room it has for them is not "
    "known. You can try it at your own risk: how it went decides whether Libre Panel writes them "
    "to this panel for everyone."
)
UNCONFIRMED = (
    'Start screens are written to this panel as to the 9.2", its sibling, but no one has said '
    "yet how they work on one. If you try it, please share how it went."
)
WRITTEN = (
    "Written. Switch the PC fully off and on again: does the panel show the boot logo when it "
    "comes on, and play the clip until Libre Panel starts? Please share how it went, also if it "
    "did not work."
)
FORM = "https://github.com/syntensa/libre-panel/issues/new?template=start_screen.yml&"
TOO_FINE = (
    "This look has more detail than fits in the room Libre Panel uses on this panel, and it "
    "is not written coarser: choose another look."
)
LARGER = (
    "The panel holds larger files than Libre Panel writes now. They can stay; written again, "
    "they become smaller."
)


def test_the_start_screen_writes_to_the_models_it_has_been_done_on(app_url, isolated_home):  # noqa: F811
    """Issue 52: start screens are written to the 9.2" and the 12.3", and to the 8.8", the
    9.2"'s sibling. For another panel of the family (a 5.2" here) the card says no one has tried
    it, and writes there only after saying what may happen, at its owner's own risk (untested);
    then it asks them to share how it went, in the issue form filled in with what was written.
    The pictures show and download, and the factory logo can still come back. Chosen for an
    8.8", the card asks to share how it went too; for a 9.2", it says nothing of it."""
    from urllib.parse import parse_qs, urlsplit

    from libre_panel import __version__

    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-5.2-usb"\nrotate = 180\n', encoding="utf-8"
    )
    asked = []

    def answer(route, request):
        if request.method != "POST":
            return route.continue_()
        body = json.loads(request.post_data)
        asked.append(body)
        reply = {"model": body["model"], "boot_logo": 47000 if body["action"] == "logo" else 0,
                 "paused": False}  # fmt: skip
        route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))

    def shared() -> dict:
        href = card.get_by_role("link", name="Share how it went").get_attribute("href")
        assert href.startswith(FORM), href
        return {key: value[0] for key, value in parse_qs(urlsplit(href).query).items()}

    with browser_page(app_url, "settings/panel", routes={"**/api/start-screen": answer}) as page:
        card = page.locator("#start-screen")
        expect(card.locator(".note-text").first).to_have_text(UNTRIED)
        expect(card.locator(".note")).to_have_count(2)  # (and that it is written turned round)
        expect(card.locator(".note").nth(1)).to_contain_text("mounted upside down")
        expect(card.locator(".boot-picture img")).to_have_count(2)
        for n in range(2):
            expect(card.get_by_role("button", name="Put on the panel").nth(n)).to_be_enabled()
        expect(card.get_by_role("link", name="JPEG")).to_be_visible()
        expect(card.get_by_role("link", name="H.264")).to_be_visible()
        assert shared() == {"template": "start_screen.yml", "title": 'Start screen: Turing 5.2"',
                            "model": "turing-5.2-usb", "version": __version__,
                            "look": "libre-panel"}  # fmt: skip
        logo = card.get_by_role("button", name="Put on the panel").first
        logo.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_contain_text("Write to a panel no one has tried?")
        expect(dialog).to_contain_text("(issue 52)")
        expect(dialog).to_contain_text("The factory logo is not touched")
        dialog.get_by_role("button", name="Cancel").click()  # told, and nothing written
        assert asked == []
        logo.click()
        page.get_by_role("button", name="Write at my own risk").click()
        expect(page.locator(".toast")).to_contain_text("The boot logo is on the panel (46 KB)")
        assert asked == [{"action": "logo", "model": "turing-5.2-usb", "panel": 0,
                          "design": "libre-panel", "untested": True}]  # fmt: skip
        expect(card.locator(".note-text").first).to_have_text(WRITTEN)
        assert shared()["written"] == "boot logo 46 KB"
        card.get_by_role("button", name="Factory logo again").click()
        page.get_by_role("button", name="Factory logo again").last.click()
        expect(page.locator(".toast").last).to_contain_text("The factory logo shows again")
        assert [body["action"] for body in asked] == ["logo", "remove-logo"]
        assert "untested" not in asked[-1]  # (taking it off is no write)
        card.locator("select").select_option("turing-8.8-usb")
        expect(card.locator(".note-text").first).to_have_text(UNCONFIRMED)
        assert shared()["model"] == "turing-8.8-usb" and "written" not in shared()
        card.get_by_role("button", name="Put on the panel").first.click()
        expect(page.get_by_role("dialog")).not_to_contain_text("no one has tried")
        page.get_by_role("button", name="Write it").click()
        expect(card.locator(".note-text").first).to_have_text(WRITTEN)
        assert asked[-1] == {"action": "logo", "model": "turing-8.8-usb", "panel": 0,
                             "design": "libre-panel"}  # fmt: skip
        card.locator("select").select_option("turing-9.2-usb")
        for n in range(2):
            expect(card.get_by_role("button", name="Put on the panel").nth(n)).to_be_enabled()
        expect(card.locator(".note")).to_have_count(1)  # (it is written turned round)
        expect(card.locator(".note")).to_contain_text("mounted upside down")
        expect(card.get_by_role("link", name="Share how it went")).to_have_count(0)


def test_the_start_screen_says_when_the_panel_holds_files_of_before(app_url, isolated_home):  # noqa: F811
    """What is on the panel, asked of one that 0.7.9 to 0.9.1 wrote to: its files are larger
    than Libre Panel writes now, which the card says; on one whose files are within that, it
    says nothing."""
    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-12.3-usb"\n', encoding="utf-8"
    )
    held = {"boot_logo": 223_833, "standby_clip": 593_754}

    def answer(route, request):
        if request.method != "POST":
            return route.continue_()
        body = json.loads(request.post_data)
        reply = {"model": body["model"], "files": ["app.cfg", "boot.jpg", "standby.h264"],
                 **held, "room": {"clip": 500_000, "logo": 150_000}, "paused": False}  # fmt: skip
        route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))

    with browser_page(app_url, "settings/panel", routes={"**/api/start-screen": answer}) as page:
        card = page.locator("#start-screen")
        card.get_by_role("button", name="What is on the panel").click()
        expect(card).to_contain_text("Boot logo: 219 KB · standby clip: 580 KB")
        expect(card.locator(".note")).to_have_text(LARGER)
        held.update(boot_logo=141_864, standby_clip=492_450)
        card.get_by_role("button", name="What is on the panel").click()
        expect(card).to_contain_text("Boot logo: 139 KB · standby clip: 481 KB")
        expect(card.locator(".note")).to_have_count(0)


def test_a_look_that_does_not_fit_finely_is_said_and_not_offered(app_url, isolated_home):  # noqa: F811
    """Levels has more detail than fits finely in the room its files get on a 9.2" or a 12.3":
    chosen there, the card says so and neither writes it nor gives its files; another look
    does both."""
    choose_theme(isolated_home, "lumen")
    (isolated_home / "config.toml").write_text(
        'theme = "lumen"\n[device]\nmodel = "turing-9.2-usb"\n', encoding="utf-8"
    )
    with browser_page(app_url, "settings/panel") as page:
        card = page.locator("#start-screen")
        looks = card.get_by_role("radiogroup", name="Look of the start screen")
        looks.get_by_role("radio", name="Levels").click()
        expect(card.locator(".note")).to_have_text(TOO_FINE)
        for n in range(2):
            expect(card.get_by_role("button", name="Put on the panel").nth(n)).to_be_disabled()
        expect(card.get_by_role("link", name="JPEG")).to_have_count(0)
        expect(card.get_by_role("link", name="H.264")).to_have_count(0)
        expect(card.locator(".boot-picture img")).to_have_count(2)  # (it still shows)
        looks.get_by_role("radio", name="Pulse").click()
        expect(card.locator(".note")).to_have_count(0)
        for n in range(2):
            expect(card.get_by_role("button", name="Put on the panel").nth(n)).to_be_enabled()
        expect(card.get_by_role("link", name="JPEG")).to_be_visible()
        expect(card.get_by_role("link", name="H.264")).to_be_visible()


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
        expect(card.locator(".note-text")).to_have_text(UNTRIED)  # (no one has tried it)
        assert "turing-4.6-usb" in card.locator(".boot-picture img").first.get_attribute("src")


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
