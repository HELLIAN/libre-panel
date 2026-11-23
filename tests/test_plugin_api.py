"""The plugin API that stays with the plugin host (docs/RUST.md, R12 the switch): plugins found
in their folders and refused for another API or for not importing, services kept in step with
the config and stopped without a trace, the types of their options, the themes the services ask
for by priority, and what a page of a plugin is told. Without the Python app: the plugin host
keeps these modules (tests/without_app.py)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from libre_panel.config import Config, ServicesConfig, log_file
from libre_panel.plugins import Delivery, PageContext, PluginHost, ServiceManager, discover
from libre_panel.plugins.loader import API_VERSION, BUILT_IN_PARTS

OWN_SERVICES = sorted(BUILT_IN_PARTS["services"])  # Libre Panel's own, no plugins


SERVICE = """
from libre_panel.plugins import Service

events = []


class Counter(Service):
    name = "counter"
    api = 1
    options = {"step": ("int", 1), "label": ("string", "Count")}

    def start(self):
        events.append(("start", dict(self.options)))
        self.host.publish("demo.count", 0, "", self.options["label"])
        self.host.on("theme-changed", lambda theme: events.append(("theme", theme)))
        self.host.on("panel-connected", lambda: events.append(("connected",)))

    def stop(self):
        events.append(("stop",))


class NoApi(Service):
    name = "no-api"


class Broken(Service):
    name = "broken"
    api = 1

    def start(self):
        raise RuntimeError("cannot reach the game")
"""


SERVICE_PARTS = {
    "libre_panel.services": {"counter": "Counter", "no-api": "NoApi", "broken": "Broken"}
}


def events_of(package):
    return sys.modules[package].events


def test_plugins_for_another_api_are_refused(plugin_folder):
    plugin_folder(SERVICE, api=API_VERSION + 1, parts=SERVICE_PARTS)
    registry = discover()
    assert registry.names("services") == OWN_SERVICES  # the plugin's are refused
    assert "written for plugin API 2" in registry.errors[0]


def test_a_plugin_that_does_not_import_is_reported(plugin_folder):
    plugin_folder("raise ImportError('needs pypresentmon')\n", parts=SERVICE_PARTS)
    registry = discover()
    assert registry.get("services", "counter") is None
    assert "needs pypresentmon" in registry.parts["services"]["counter"].error


def services_config(enabled, **options):
    return Config(services=ServicesConfig(enabled=list(enabled), options=options))


def test_services_follow_the_config(plugin_folder):
    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    manager.apply(services_config(["counter", "broken", "missing"], counter={"step": 5}))
    assert manager.state["counter"] == "running"
    assert manager.state["broken"].startswith("failed: RuntimeError")
    assert manager.state["missing"] == "not installed"
    assert events_of(package) == [("start", {"step": 5, "label": "Count"})]
    assert host.published()["demo.count"].label == "Count"

    # changed options restart it; a wrong type is refused and reported
    manager.apply(services_config(["counter"], counter={"step": 6}))
    assert events_of(package)[-2:] == [("stop",), ("start", {"step": 6, "label": "Count"})]
    manager.apply(services_config(["counter"], counter={"step": "six"}))
    assert manager.state["counter"].startswith("failed: services.counter.step: expected int")

    manager.apply(services_config(["counter"]))
    manager.stop()
    assert events_of(package)[-1] == ("stop",) and not manager.running
    host.close()


def test_a_stopped_service_leaves_nothing_behind(plugin_folder):
    from PIL import Image

    from libre_panel.sensors.base import Reading

    package = plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    manager.apply(services_config(["counter"]))
    service = manager.running["counter"][0]
    quits = []
    service.host.on("quit", lambda: quits.append(len(manager.running)))
    service.host.set_mode("game")
    service.host.show_theme("slate")
    service.host.publish_image("demo.cover", Image.new("RGB", (2, 2)))
    host.publish(Reading("x.y", 1), owner="another")  # someone else's reading stays

    manager.apply(services_config([]))
    assert host.mode is None and host.theme is None  # a game mode must not outlive its service
    assert "demo.count" not in host.published() and host.images() == {}
    assert "x.y" in host.published()
    before = list(events_of(package))
    host.emit("panel-connected", wait=True)
    assert events_of(package) == before  # no more events for a stopped service

    manager.apply(services_config(["counter"]))
    manager.running["counter"][0].host.on("quit", lambda: quits.append(len(manager.running)))
    manager.stop()
    assert quits == [1]  # "quit" arrives while the service still runs
    host.close()


def test_option_types():
    from libre_panel.plugins.host import apply_options
    from libre_panel.plugins.loader import PluginError

    schema = {"fps": ("int", 30), "scale": ("number", 1.0), "on": ("bool", True)}
    assert apply_options(schema, {"scale": 2}, "x") == {"fps": 30, "scale": 2, "on": True}
    for bad in ({"fps": True}, {"fps": 2.5}, {"on": "yes"}):
        with pytest.raises(PluginError):
            apply_options(schema, bad, "x")


def test_theme_requests_by_priority():
    host = PluginHost()
    host.request_theme("a", by="autopilot", transition="slide")
    assert host.theme == "a" and host.take_transition() == "slide"
    assert host.take_transition() is None  # once
    host.request_theme("report", by="game", priority=10)
    host.request_theme("b", by="autopilot", transition="fade")  # below the report: nothing new
    assert host.theme == "report" and host.take_transition() is None
    host.request_theme("c", by="clock")  # same priority as the autopilot, asked later
    host.request_theme(None, by="game", transition=("demo.entrance", {"x": 1}))
    assert host.theme == "c" and host.take_transition() == ("demo.entrance", {"x": 1})
    host.forget("clock")
    assert host.theme == "b"
    before = host.switches
    host.set_mode("game", by="game", transition="demo.entrance")
    assert host.switches == before + 1 and host.take_transition() == "demo.entrance"
    host.set_mode(None, by="game")  # without a transition: not a switch of its own
    assert host.switches == before + 1


def test_a_page_can_tell_that_libre_panel_runs_with_the_highest_rights(monkeypatch):
    """A page leaves what names a program or a file to config.toml then (docs/PLUGINS.md)."""
    from libre_panel import rights
    from libre_panel.plugins import PageContext

    context = PageContext(None)
    assert context.elevated is False  # (the tests run with the rights of any user)
    monkeypatch.setattr(rights, "elevated", lambda: True)
    assert context.elevated is True


def test_page_context_finds_running_services(plugin_folder):
    from libre_panel.plugins import PageContext

    plugin_folder(SERVICE, parts=SERVICE_PARTS)
    host = PluginHost()
    manager = ServiceManager(host, discover())
    manager.apply(services_config(["counter"]))

    class Controls:
        services = manager
        plugin_host = host
        config_path = None

    context = PageContext(Controls())
    assert context.service("counter") is manager.running["counter"][0]
    assert context.services() == {"counter": "running"}
    assert context.snapshot() is None  # the panel has not drawn yet
    first = manager.running["counter"][0]
    assert context.restart_service("counter") == "running"
    assert manager.running["counter"][0] is not first  # a new instance, same options
    with pytest.raises(LookupError):
        context.restart_service("gamemode")  # not enabled
    assert context.config_path.name == "config.toml"
    plain = PageContext(None, config_path=Path("/tmp/other.toml"))
    assert plain.service("counter") is None and plain.services() == {} and plain.snapshot() is None
    assert plain.config_path == Path("/tmp/other.toml")
    manager.stop()
    host.close()


# -- a page's context -----------------------------------------------------------------------------


def test_a_page_context_has_the_app_follow_config_toml():
    from types import SimpleNamespace

    from libre_panel.plugins import PageContext

    asked = []
    PageContext(SimpleNamespace(follow_config=lambda: asked.append(1))).follow_config()
    assert asked == [1]
    PageContext().follow_config()  # a plain editor: no services, nothing to follow
    PageContext(SimpleNamespace()).follow_config()  # an app that cannot be asked: no failure


# -- what a page can tell -------------------------------------------------------------------------


def test_page_context_knows_whether_the_background_app_serves_the_editor():
    plain = PageContext(None)
    assert plain.background is False and plain.shown_frame() is None
    host = PluginHost()
    picture = Image.new("RGB", (8, 4), "red")
    host.shown = picture
    context = PageContext(SimpleNamespace(plugin_host=host))
    assert context.background is True and context.shown_frame() is picture
    host.close()


def test_the_log_tail_is_read_from_the_end_of_the_log(isolated_home):
    context = PageContext(None)
    assert context.log_tail() == []  # no log yet
    path = log_file()
    assert path == isolated_home / "logs" / "libre-panel.log"  # where the tray app writes it
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"line {n}\n" for n in range(1, 5001)), encoding="utf-8")
    assert context.log_tail(3) == ["line 4998", "line 4999", "line 5000"]
    assert len(context.log_tail(40)) == 40 and len(context.log_tail(99999)) == 1000
    assert context.log_tail(0) == ["line 5000"]  # at least one
    path.write_text("one\ntwo\n", encoding="utf-8")
    assert context.log_tail() == ["one", "two"]  # shorter than asked: all of it
    path.write_text("".join(f"{n} {'x' * 3000}\n" for n in range(50)), encoding="utf-8")
    assert [row.split()[0] for row in context.log_tail(3)] == ["47", "48", "49"]  # long lines
    path.write_bytes(b"caf\xe9 \xff\nnext\n")  # not UTF-8: still readable
    assert context.log_tail(2)[1] == "next" and len(context.log_tail(2)) == 2


def test_a_delivery_is_true_when_the_message_will_show():
    assert Delivery(True) and not Delivery(False, "theme")
    assert Delivery(False, "screen").reason == "screen" and Delivery(True).reason == ""
