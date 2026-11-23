"""What the Python package holds since the Python app is gone (docs/RUST.md, R12 the switch):
the plugin host and the plugin API, and what that stands on (test_plugin_host.py checks that it
reaches no more and that the package holds no more), and the Windows wheel's command."""

KEPT = frozenset({
    "libre_panel", "libre_panel.accent", "libre_panel.bundled", "libre_panel.cards",
    "libre_panel.config", "libre_panel.datadir", "libre_panel.devices",
    "libre_panel.devices.base",  # (only by the package's own names: the models load no driver)
    "libre_panel.devices.models", "libre_panel.fonts", "libre_panel.fonts.fallback",
    "libre_panel.hotkeys", "libre_panel.i18n", "libre_panel.plugin_host", "libre_panel.plugins",
    "libre_panel.plugins.drawing", "libre_panel.plugins.host", "libre_panel.plugins.loader",
    "libre_panel.plugins.pages", "libre_panel.plugins.render", "libre_panel.presence",
    "libre_panel.render", "libre_panel.render.cover", "libre_panel.rights", "libre_panel.sensors",
    "libre_panel.sensors.base", "libre_panel.sensors.sky", "libre_panel.sequence",
})  # fmt: skip
# what the wheels for pip and pipx bring besides: on Windows the command that runs the Rust app
# (packaging/platform_wheel.py), which imports nothing of Libre Panel's
WHEEL = frozenset({"libre_panel.launch"})
