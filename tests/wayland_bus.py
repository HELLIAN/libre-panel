"""Stand-ins on a session bus of the test's own, for the screen on Wayland: GNOME's screen cast
(Mutter's session with a monitor or a virtual one and its stream's node), the screen-sharing
portal (its requests answered by their Response signals, the dialog's choice kept, the
PipeWire connection handed over as a file descriptor) and GNOME's monitors. The bus itself is
conftest.py's ``bus``."""

import itertools
import os
import threading

from jeepney import (
    DBusAddress,
    HeaderFields,
    MessageType,
    new_error,
    new_method_return,
    new_signal,
)
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection


class Service(threading.Thread):
    """A stand-in on the bus: ``name``, answering method calls by (interface, member)."""

    def __init__(self, name):
        super().__init__(daemon=True)
        self.connection = open_dbus_connection(bus="SESSION", enable_fds=True)
        self.connection.send_and_get_reply(message_bus.RequestName(name))
        self.name, self.calls, self.stop = name, [], threading.Event()
        self.start()

    def answer(self, message):  # -> (signature, body, signals to send after the reply)
        raise NotImplementedError

    def run(self):
        while not self.stop.is_set():
            try:
                message = self.connection.receive(timeout=0.1)
            except TimeoutError:
                continue
            header = message.header
            if header.message_type != MessageType.method_call:
                continue
            member = header.fields[HeaderFields.member]
            self.calls.append((member, message.body))
            try:
                signature, body, signals = self.answer(message)
            except LookupError:
                self.connection.send(new_error(message, "org.freedesktop.DBus.Error.UnknownMethod"))
                continue
            self.connection.send(new_method_return(message, signature, body))
            for signal in signals:
                self.connection.send(signal)

    def close(self):
        self.stop.set()
        self.join(2)
        self.connection.close()


class Mutter(Service):
    SESSION = "/org/gnome/Mutter/ScreenCast/Session/u1"
    STREAM = "/org/gnome/Mutter/ScreenCast/Stream/u1"

    def __init__(self):
        super().__init__("org.gnome.Mutter.ScreenCast")

    def answer(self, message):
        member = message.header.fields[HeaderFields.member]
        if member == "CreateSession":
            return "o", (self.SESSION,), []
        if member in ("RecordVirtual", "RecordMonitor"):
            return "o", (self.STREAM,), []
        if member == "Start":
            stream = DBusAddress(self.STREAM, interface="org.gnome.Mutter.ScreenCast.Stream")
            return "", (), [new_signal(stream, "PipeWireStreamAdded", "u", (42,))]
        if member == "Stop":
            session = DBusAddress(self.SESSION, interface="org.gnome.Mutter.ScreenCast.Session")
            return "", (), [new_signal(session, "Closed")]
        raise LookupError(member)


class Portal(Service):
    def __init__(self, answer_code=0):
        super().__init__("org.freedesktop.portal.Desktop")
        self.code = answer_code
        self.read, self.write = os.pipe()
        self.sessions = itertools.count(1)

    def respond(self, message, options, results):
        sender = message.header.fields[HeaderFields.sender].lstrip(":").replace(".", "_")
        token = options["handle_token"][1]
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        request = DBusAddress(path, interface="org.freedesktop.portal.Request")
        return "o", (path,), [new_signal(request, "Response", "ua{sv}", (self.code, results))]

    def answer(self, message):
        member = message.header.fields[HeaderFields.member]
        body = message.body
        if member == "CreateSession":
            session = f"/org/freedesktop/portal/desktop/session/1_1/s{next(self.sessions)}"
            return self.respond(message, body[0], {"session_handle": ("s", session)})
        if member == "SelectSources":
            return self.respond(message, body[1], {})
        if member == "Start":
            stream = (77, {"size": ("(ii)", (1920, 1080)), "source_type": ("u", 4)})
            return self.respond(message, body[2], {"streams": ("a(ua{sv})", [stream]),
                                                   "restore_token": ("s", "kept-1")})  # fmt: skip
        if member == "OpenPipeWireRemote":
            return "h", (self.read,), []
        if member == "Close":
            return "", (), []
        raise LookupError(member)


DISPLAY_STATE = (
    7,
    [(("DP-1", "DEL", "U2720Q", "1"),
      [("3840x2160@60", 3840, 2160, 60.0, 1.0, [1.0, 2.0], {"is-current": ("b", True)}),
       ("1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0], {})],
      {"display-name": ("s", "Dell 27\"")}),
     (("HDMI-1", "ACR", "X", "2"),
      [("1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": ("b", True)})],
      {})],
    [(0, 0, 1.0, 0, True, [("DP-1", "DEL", "U2720Q", "1")], {}),
     (3840, 0, 1.0, 0, False, [("HDMI-1", "ACR", "X", "2")], {})],
    {},
)  # fmt: skip


class DisplayConfig(Service):
    def __init__(self):
        super().__init__("org.gnome.Mutter.DisplayConfig")

    def answer(self, message):
        signature = "ua((ssss)a(siiddada{sv})a{sv})a(iiduba(ssss)a{sv})a{sv}"
        return signature, DISPLAY_STATE, []
