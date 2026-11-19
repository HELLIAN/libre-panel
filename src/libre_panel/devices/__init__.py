"""The panels' drivers (base.py and one module a kind) and the models they drive (models.py).
The names of base.py are the package's too; it is loaded when one is first asked for, not with
the package: what only reads the models (a plugin's context) loads no driver."""

from typing import Any

__all__ = ["DeviceError", "Display", "available_drivers", "create_display", "list_serial_ports"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from libre_panel.devices import base

        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
