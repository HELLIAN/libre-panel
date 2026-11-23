"""What of the Python package stays once the Python app is gone (docs/RUST.md, R12 the switch):
the plugin host and the plugin API, and what that stands on (test_plugin_host.py checks that it
reaches no more), and the Windows wheel's command. ``LP_WITHOUT_APP=1`` runs the tests as they
run then: any other module of libre_panel cannot be imported, so a test that still asks the
Python app fails."""

from __future__ import annotations

import importlib.abc
import os
import sys
from typing import Any

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

ON = bool(os.environ.get("LP_WITHOUT_APP"))


class _Gone(importlib.abc.MetaPathFinder):
    def find_spec(self, name: str, path: Any = None, target: Any = None) -> None:
        if name.split(".")[0] == "libre_panel" and name not in KEPT | WHEEL:
            raise ModuleNotFoundError(f"{name} is the Python app's (LP_WITHOUT_APP)", name=name)


def take_away() -> None:
    """The Python app's modules out of reach, where LP_WITHOUT_APP asks for it."""
    if ON and not any(isinstance(finder, _Gone) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Gone())
