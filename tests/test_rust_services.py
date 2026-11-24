"""rust/services against the Python app (docs/RUST.md): the host between the services and
the main loop (plugins/host.py), as the Rust app has it."""

from __future__ import annotations

import json
import os
import random
from typing import Any

import golden
import pytest
from rust_check import run

from libre_panel.plugins import host as hosts
from libre_panel.plugins.loader import PluginError
from libre_panel.sensors.base import Reading


def test_the_services_host_holds_what_pythons_holds() -> None:
    """plugins/host.py's PluginHost: readings and states published and taken back, the
    theme the services ask for (by priority, then the latest, in the modes each request
    counts in), the mode and who set it, the transition of a switch, the messages kept for
    the main loop, a service that stops and leaves nothing; the mode changes a listener
    hears; and a service's options from its defaults and config.toml's."""
    rng = random.Random(0x5E81)
    services = ["game", "autopilot", "timer", "notices", None]
    themes = ["arena", "slate", "lumen", "", None]
    modes = ["game", "music", None, None, "*"]
    keys = ["game.fps", "timer.left", "notice.state", "a.b"]
    runs = []
    for _ in range(300):
        steps: list[list[Any]] = []
        for _ in range(rng.randrange(1, 40)):
            kinds = ["publish", "state", "unpublish", "theme", "theme", "theme", "mode", "mode",
                     "forget", "notify", "take_toasts", "take_transition"]  # fmt: skip
            kind = rng.choice(kinds)
            if kind == "publish":
                value = rng.choice([1, 2.5, "text", None, True])
                steps.append([kind, rng.choice(keys), value, rng.choice(["", "fps"]), "Label",
                              rng.choice(services)])  # fmt: skip
            elif kind == "state":
                value = rng.choice([{"a": [1, "ü", None]}, [1, 2.5], None, {}, 5, "text", True])
                steps.append([kind, rng.choice(keys), value, rng.choice(services), "State"])
            elif kind == "unpublish":
                steps.append([kind, rng.choice(keys)])
            elif kind == "theme":
                steps.append([kind, rng.choice(themes), rng.choice(services[:4]),
                              rng.choice([None, "fade", "slide"]), rng.choice([0, 0, 5, -1]),
                              rng.choice(modes)])  # fmt: skip
            elif kind == "mode":
                steps.append([kind, rng.choice(modes[:4]), rng.choice(services),
                              rng.choice([None, None, "fade"])])  # fmt: skip
            elif kind == "forget":
                steps.append([kind, rng.choice(services[:4])])
            elif kind == "notify":
                steps.append([kind, f"message {rng.randrange(99)}", rng.choice(["", "music"]),
                              rng.randrange(3), "notices"])  # fmt: skip
            else:
                steps.append([kind])
        if rng.random() < 0.2:  # (more messages than the host keeps)
            steps += [["notify", f"m{n}", "", 0, "notices"] for n in range(25)]
            steps.append(["take_toasts"])
        runs.append(steps)

    schema = [["limit", "int", 5], ["rate", "number", 2.5], ["on", "bool", True],
              ["name", "string", "x"], ["list", "list", ["a"]]]  # fmt: skip
    values = [1, 2.5, True, False, "text", ["a", 1], {"a": 1}, None, 10**30, -0.0]
    options = []
    for _ in range(400):
        given = {key: rng.choice(values)
                 for key in rng.sample(["limit", "rate", "on", "name", "list", "other", "cloud",
                                        "cloud_token"], rng.randrange(0, 4))}  # fmt: skip
        options.append([schema, given, rng.choice(["services.game", "services.agents"])])
    sent = [[schema, json.dumps(given), where] for schema, given, where in options]
    [ours] = run([{"op": "services_host", "runs": runs, "options": sent}])

    def value_of(value: Any) -> Any:
        return {"float": repr(value)} if isinstance(value, float) else value

    seen = {"themes": 0, "modes": 0, "errors": 0, "toasts": 0, "unknown": 0, "heard": 0}
    for steps, (found, heard) in zip(runs, ours["runs"], strict=True):
        host = hosts.PluginHost(clock=lambda: 12.5)
        modes_heard: list[Any] = []
        host.listen("mode-changed", lambda mode, heard=modes_heard: heard.append(mode))
        for step, got in zip(steps, found, strict=True):
            kind, said = step[0], None
            if kind == "publish":
                host.publish(Reading(step[1], step[2], step[3], step[4]), owner=step[5])
            elif kind == "state":
                try:
                    host.publish_state(step[1], step[2], owner=step[3], label=step[4])
                except TypeError as exc:
                    said = {"error": str(exc)}
                    seen["errors"] += 1
            elif kind == "unpublish":
                host.unpublish(step[1])
            elif kind == "theme":
                host.request_theme(step[1], by=step[2], transition=step[3], priority=step[4],
                                   mode=step[5])  # fmt: skip
            elif kind == "mode":
                host.set_mode(step[1], by=step[2], transition=step[3])
            elif kind == "forget":
                host.forget(step[1])
            elif kind == "notify":
                delivery = host.notify(hosts.Toast(step[1], kind=step[2], rank=step[3],
                                                   service=step[4]))  # fmt: skip
                said = [delivery.shown, delivery.reason]
            elif kind == "take_toasts":
                said = [[t.text, t.kind, t.rank, t.service] for t in host.take_toasts()]
                seen["toasts"] += len(said) == hosts.PluginHost.MAX_TOASTS
            else:
                said = host.take_transition()
            published = [[key, value_of(r.value), r.unit, r.label, r.stamp]
                         for key, r in host.published().items()]  # fmt: skip
            wanted = [said, published, host.theme, host.theme_owner, host.mode, host.mode_owner,
                      host.switches]  # fmt: skip
            assert got == wanted, (steps, step)
            seen["themes"] += host.theme is not None
            seen["modes"] += host.mode is not None
        host.close()
        assert heard == modes_heard, steps
        seen["heard"] += len(heard)
    for (_, given, where), got in zip(options, ours["options"], strict=True):
        try:
            wanted = hosts.apply_options({key: (kind, default) for key, kind, default in schema},
                                         given, where)  # fmt: skip
        except PluginError as exc:
            assert got == {"error": str(exc)}, given
            seen["errors"] += 1
            continue
        assert "error" not in got, (given, got)
        assert got[0] == [[key, repr(value)] for key, value in wanted.items()], given
        seen["unknown"] += len(got[1])
    assert all(count > 30 for count in seen.values()), seen


class _Host:
    """A service's host for a test: what it published last, and what it said."""

    def __init__(self) -> None:
        self.published: dict[str, list[Any]] = {}
        self.said: list[str] = []

    def publish(self, key: str, value: Any, unit: str = "", label: str = "") -> None:
        self.published[key] = [key, value, unit, label]

    def notify(self, text: str, **_: Any) -> None:
        self.said.append(text)

    def on(self, *_: Any) -> None:
        pass


@pytest.mark.kept
def test_the_timer_counts_as_timer_py_counts() -> None:
    """services/timer.py: started, paused and set back at moments of a clock, the time that
    is up (a plain timer; focus and break in turn), what it says then and its readings; and
    the clock's text of seconds."""
    from libre_panel import i18n

    rng = random.Random(0x7133)
    runs = []
    for _ in range(300):
        minutes = rng.choice([25, 0.05, 0.1, 1, 0, 90])
        breaks = rng.choice([0, 0, 0.05, 5, -1])
        now, steps = rng.uniform(0, 1000), []
        for _ in range(rng.randrange(1, 60)):
            now += rng.choice([0.2, 0.2, 1.0, 2.9, 3.0, 30.0, 400.0])
            kind = rng.choice(["toggle", "tick", "tick", "tick", "reset"])
            steps.append([kind, now])
        runs.append([minutes, breaks, steps])
    seconds = [0, 0.1, 1, 59.0, 59.01, 60, 61.5, 3599.5, 3600, 3661.2, 86400.0, -5, 1e7, 0.999]
    for language in ("en", "de"):
        [ours] = run([{"op": "timer", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "runs": runs, "seconds": seconds}])  # fmt: skip
        assert len(ours["texts"]) == len(seconds)
        seen = {"up": 0, "paused": 0}
        for (_minutes, breaks, steps), found in zip(runs, ours["runs"], strict=True):
            for (kind, _at), (told, readings) in zip(steps, found, strict=True):
                said = {key: value for key, value, *_ in readings}
                if told:  # (the time is up: a plain timer's, or that of the turn that begins)
                    turn = "up" if breaks <= 0 else said["timer.phase"]
                    seen[turn] = seen.get(turn, 0) + 1
                seen["paused"] += kind == "toggle" and not said["timer.running"]
        # (a plain timer up, focus and break each begun, the timer paused)
        assert len(seen) == 4 and all(count > 20 for count in seen.values()), seen


@pytest.mark.kept
def test_power_is_counted_as_power_py_counts_it(tmp_path: Any) -> None:
    """services/power.py: the watts of the sensors named and the rest, the energy of the day
    and the month they add up to between samples (not over a pause), their cost, a new day
    and a new month, the totals kept before and those it keeps."""
    from datetime import datetime, timedelta

    rng = random.Random(0x90E4)
    runs = []
    for n in range(300):
        sensors = rng.choice([["cpu.power", "gpu.power"], ["cpu.power"], [], ["psu.out", " ", 5]])
        rest = rng.choice([0, 50, 62.5, -10])
        price = rng.choice([0, 0.3, 0.2875, -1])
        wall = datetime(2026, rng.randrange(1, 13), rng.randrange(1, 29), rng.randrange(24),
                        rng.randrange(60), rng.randrange(60))  # fmt: skip
        day = wall.strftime("%Y-%m-%d")
        totals = json.dumps({
            "day": rng.choice([day, day, day, "2020-01-01", 5]),
            "today_wh": rng.choice([12.5, 7, 0.125, "x", None, True, 1e400]),
            "month": rng.choice([day[:7], day[:7], "2020-01"]),
            "month_wh": rng.choice([345.678, 0, None]),
        })  # fmt: skip
        kept = rng.choice([None, "", "[1]", "{broken", totals, totals, totals, totals])
        now, steps = rng.uniform(0, 1000), []
        for _ in range(rng.randrange(1, 40)):
            pause = rng.choice([1.0, 1.0, 1.0, 0.5, 10.0, 10.5, 0.0, 3600.0, 86400.0])
            now += pause
            wall += timedelta(seconds=pause)
            readings = {key: rng.choice([65.5, 120, 0, -5, None, True, "9", 2**60, 33.333])
                        for key in ("cpu.power", "gpu.power", "psu.out", "5")
                        if rng.random() < 0.8}  # fmt: skip
            steps.append([now, [wall.year, wall.month, wall.day, wall.hour, wall.minute,
                                wall.second, 0], readings])  # fmt: skip
        runs.append([sensors, rest, price, rng.choice(["€", "$", "kr"]), kept, steps, n])
    [ours] = run([{"op": "power", "runs": [r[:6] for r in runs]}])
    assert "runs" in ours, ours

    seen = {"cost": 0, "kept": 0, "new day": 0, "no sensor": 0}
    for (_sensors, _rest, _price, _currency, kept, steps, _n), (found, saved) in zip(
        runs, ours["runs"], strict=True
    ):
        # (a step's readings: the watts, the day's and the month's energy, and their cost
        # where there is a price: key, value, unit, label)
        today = found[0][1][1]
        seen["kept"] += kept is not None and float(today["float"]) > 0  # (the day's, kept before)
        day = None
        for (_now, wall, _readings), rows in zip(steps, found, strict=True):
            seen["cost"] += len(rows) == 5
            seen["new day"] += day is not None and wall[:3] != day
            seen["no sensor"] += rows[0][1] is None
            day = wall[:3]
        assert json.loads(saved)["day"] == "{:04d}-{:02d}-{:02d}".format(*day)  # (what it keeps)
    assert all(count > 15 for count in seen.values()), seen


def _plain_rows(rows: Any) -> list[list[Any]]:
    return [[key, {"float": repr(value)} if isinstance(value, float) else value]
            for key, value in rows]  # fmt: skip


def _floated(value: Any) -> Any:
    return {"float": repr(float(value))} if value is not None else None


def _agent_event(rng: random.Random) -> dict[str, Any]:
    names = golden.given("agents")  # (agents.py's names, as they were when its answers were kept)
    events = [
        *names["working"],
        *names["idle"],
        *names["hook_events"],
        "",
        None,
        5,
        "Weird" * 12,
        *["PermissionRequest"] * 6,
    ]
    notices = [*names["waiting_notices"], "idle_prompt", "auth_success", None, 3]
    data: dict[str, Any] = {"hook_event_name": rng.choice(events)}
    if rng.random() < 0.9:
        data["session_id"] = rng.choice(["a", "b", "c", "d", "", None, 7, "ä" * 40])
    if rng.random() < 0.6:
        data["tool_name"] = rng.choice(["Bash", "", None, 12, "Edit" * 20, ["x"]])
    if rng.random() < 0.6:
        data["session_title"] = rng.choice([" Fix   the\tbug\n now ", "", "   ", None, 5,
                                            "x" * 200, "Größe ändern"])  # fmt: skip
    if rng.random() < 0.7:
        data["notification_type"] = rng.choice(notices)
    return data


def _agent_status(rng: random.Random, now: float) -> Any:
    from datetime import UTC, datetime

    def moment() -> Any:
        later = now + rng.choice([-50, 600, 18000, 600000])
        iso = datetime.fromtimestamp(later, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        return rng.choice([later, int(later), later * 1000, str(later), iso + "Z", iso,
                           iso + ".25+02:00", "soon", "", None, 0, True])  # fmt: skip

    def limit() -> Any:
        if rng.random() < 0.15:
            return rng.choice([None, 5, []])
        return {"used_percentage": rng.choice([12, 55.5, "80", 100, 101, -1, None, True,
                                               float("nan")]),
                "resets_at": moment()}  # fmt: skip

    if rng.random() < 0.05:
        return rng.choice([None, [1], "x"])
    data: dict[str, Any] = {
        "session_id": rng.choice(["a", "b", "c", "", None]),
        "model": rng.choice([{"display_name": "Opus"}, {"display_name": "M" * 60}, {}, None,
                             {"display_name": 5}, {"display_name": ""}, "Opus"]),
        "context_window": rng.choice([{"used_percentage": rng.choice([0, 41.5, 99.5, 150,
                                                                       "33", None])}, {}, 7]),
        "cost": rng.choice([{"total_cost_usd": rng.choice([0, 0.25, 1.5, 3, "2.75", -1, None,
                                                            True, float("inf")])}, {}, None]),
    }  # fmt: skip
    if rng.random() < 0.7:
        data["rate_limits"] = {"five_hour": limit(), "seven_day": limit()}
    return data


def _without_the_keys_made(answers: list[Any]) -> list[Any]:
    """The answers without the keys made by chance: each named as a new one, where it was read
    again as it was made."""
    import re

    [ours] = answers
    keys = [
        [read, "a key" if re.fullmatch(r"[A-Za-z0-9_-]{32}", new) else new,
         "the same" if again == new else again]
        for read, new, again in ours["keys"]
    ]  # fmt: skip
    return [{**ours, "keys": keys}]


@pytest.mark.kept
def test_the_agents_board_tells_what_agents_py_tells(tmp_path: Any) -> None:
    """libre_panel/agents.py and services/agents.py: hooks, status lines and the plan's usage
    at moments of a clock and the readings, lines and notices they give; what the programs'
    answers hold; the settings with and without Libre Panel's hooks; the files and the key."""
    import hashlib
    import os
    import re
    import sys
    from pathlib import Path

    names = golden.given("agents")  # (agents.py's names and commands)
    rng = random.Random(0xA6E7)
    text = lambda value: json.dumps(value)  # noqa: E731
    boards = []
    for _ in range(250):
        now, steps = 1.78e9 + rng.uniform(0, 3e7), []
        for _ in range(rng.randrange(1, 70)):
            now += rng.choice([0.5, 0.5, 2, 2, 30, 30, 30, 600, 1000, 7300, 22000, 90000])
            agent = rng.choice(["claude", "claude", "codex", "codex", "gemini"])
            real = rng.choice(["claude", "codex"])
            what = rng.choice(
                ["hook"] * 8
                + ["status"] * 5
                + ["look"] * 4
                + ["readings"] * 3
                + ["quota", "tokens", "failed", "usage", "heard", "state"]
            )
            if what == "hook":
                data = _agent_event(rng) if rng.random() < 0.97 else rng.choice([None, [], "x"])
                steps.append([now, what, agent, text(data)])
            elif what == "status":
                steps.append([now, what, agent, text(_agent_status(rng, now))])
            elif what == "quota":
                resets = rng.choice([None, now + 900, now - 5, 0.0])
                steps.append([now, what, real, rng.choice(["five_hour", "week"]),
                              rng.choice([0.0, 37.5, 100.0]), resets,
                              rng.choice(["background", ""])])  # fmt: skip
            elif what == "tokens":
                steps.append([now, what, real, rng.choice([None, 0.0, 125000.0]),
                              rng.choice([None, 9e6])])  # fmt: skip
            elif what == "failed":
                steps.append([now, what, real, "no answer", rng.choice(["", "old"]),
                              rng.choice(["", "2.1.0"])])  # fmt: skip
            elif what == "look":
                steps.append([now, what, rng.random() < 0.7, rng.random() < 0.8])
            else:
                steps.append([now, what, real])
        boards.append(steps)

    def reset() -> Any:
        return rng.choice([1.79e9, 1.79e12, "2026-08-01T10:00:00Z", None, "x"])

    def window(used: str, minutes: bool) -> Any:
        if rng.random() < 0.05:
            return rng.choice([None, 4])
        found = {used: rng.choice([5, 62.5, 62.5, 99, "40", "40", 120, None]),
                 "resets_at" if not minutes else "resetsAt": reset()}  # fmt: skip
        if minutes:
            found["windowDurationMins"] = rng.choice(
                [300, 360, 361, 1440, 10080, 10080, 10080, None, "300"]
            )
        return found

    answers = []
    junk = ["not json", "[1]", '"x"', "{}", '{"type": "system", "subtype": "init"}',
            '{"type": "assistant"}', '{"type": null}']  # fmt: skip
    for _ in range(400):
        lines = [junk[4] for _ in range(rng.randrange(3))]
        if rng.random() < 0.5:
            limits = {"five_hour": window("utilization", False),
                      "seven_day": window("utilization", False)}  # fmt: skip
            data: Any = {"rate_limits_available": rng.choice([True] * 6 + [False, None, 1]),
                         "rate_limits": rng.choice([limits] * 6 + [{}, None])}  # fmt: skip
            response = {"request_id": rng.choice(["libre-panel-usage"] * 12 + ["other", None]),
                        "subtype": rng.choice(["success"] * 12 + ["error"]),
                        "response": rng.choice([data] * 12 + [None, [data]])}  # fmt: skip
            message: Any = {"type": "control_response",
                            "response": rng.choice([response] * 12 + [None])}  # fmt: skip
            if rng.random() < 0.1:
                lines.insert(rng.randrange(len(lines) + 1), rng.choice(junk))
            lines.append(text(message))
            answers.append(["claude", [line + "\n" for line in lines]])
        else:
            limits = {"primary": window("usedPercent", True),
                      "secondary": window("usedPercent", True)}  # fmt: skip
            result = rng.choice([{"rateLimits": limits}, {"rateLimits": limits},
                                 {"rateLimitsByLimitId": {"codex": limits}},
                                 {"rateLimits": {}, "rateLimitsByLimitId": {"codex": limits}},
                                 {"rateLimits": None, "rateLimitsByLimitId": 5}, {}])  # fmt: skip
            buckets = [rng.choice([{"startDate": rng.choice(["2026-11-10", "2026-11-10",
                                                             "2026-11-09",
                                                             "2026-11-08", "2026-11-11", None, 5]),
                                    "tokens": rng.choice([1000, 2500.5, "70", None, True])}] * 5
                                  + [None, "x"]) for _ in range(rng.randrange(8))]  # fmt: skip
            used = rng.choice([{"dailyUsageBuckets": buckets}] * 8
                              + [{"dailyUsageBuckets": None}, {}, None, [1]])  # fmt: skip
            lines += [text({"id": 1, "result": {}}), rng.choice(junk[:4]),
                      text(rng.choice([{"id": 2, "result": result}] * 8
                                      + [{"id": 2, "error": {"message": "no"}}, {"id": "2"}])),
                      text(rng.choice([{"id": 3, "result": used}] * 5
                                      + [{"id": 3.0, "result": used}, {"id": True, "result": used},
                                         {"method": "x"}]))]  # fmt: skip
            answers.append(["codex", [line + "\n" for line in lines], "2026-11-10",
                            rng.choice(["2026-11-09", "2026-11-10"])])  # fmt: skip

    versions = ["2.1.293 (Claude Code)", " \t2.1.5\n", "v2.1.3", "2.1", "10.20.30abc", "",
                "٢.١.٣", "2 .1.3", "2.1.292", "2.2.0", "3.0.0 x", "2.1.293.4", "1..2"]  # fmt: skip
    environment = [["PATH", "/bin"], ["DISABLE_TELEMETRY", "0"], ["disable_autoupdater", "x"],
                   ["HOME", "/h"], ["Claude_Code_Disable_Nonessential_Traffic", "1"],
                   ["CLAUDE_CODE_ENABLE_TELEMETRY", "1"], ["LANG", "de_DE.UTF-8"]]  # fmt: skip

    def group(ours: bool) -> Any:
        port = rng.choice([8765, 9000, 1])
        command = (names["hook_commands"][str(port)] if ours
                   else rng.choice(["echo hi", "say /api/ai", 5]))  # fmt: skip
        return rng.choice([{"hooks": [{"type": "command", "command": command}]},
                           {"matcher": "Bash", "hooks": [{"type": "command", "command": "ls"},
                                                         {"type": "command", "command": command}]},
                           {"hooks": command}, {"hooks": [command]}, 5])  # fmt: skip

    settings = []
    for _ in range(400):
        given: dict[str, Any] = {}
        if rng.random() < 0.3:
            given["model"] = rng.choice(["opus", 1.0, {"ä": [1, None]}])
        kind = rng.random()
        if kind < 0.1:
            given["hooks"] = rng.choice([None, 5, [], "x"])
        elif kind < 0.9:
            hooks: dict[str, Any] = {}
            for event in rng.sample([*names["hook_events"], "Custom"], rng.randrange(5)):
                groups = [group(rng.random() < 0.5) for _ in range(rng.randrange(4))]
                hooks[event] = rng.choice([{}, 5, None]) if rng.random() < 0.05 else groups
            given["hooks"] = hooks
        if rng.random() < 0.6:
            given["statusLine"] = rng.choice([
                {"type": "command", "command": names["status_command"]},
                {"type": "command", "command": "my-line"}, "", None, {},
                {"command": 5}])  # fmt: skip
        events = rng.sample([*names["hook_events"]], rng.randrange(1, 5))
        settings.append([text(given), rng.choice([8765, 9001]), rng.choice(["s3", "k-_9"]),
                         rng.random() < 0.6, events,
                         rng.choice(["x /api/ai/hook", "echo"])])  # fmt: skip

    contents = [None, "", "  \n", "{}", '{"a": 1, "ä": "ö"}', "[1]", "{broken", '"x"',
                '{"hooks": {"Stop": []}}']  # fmt: skip
    files = []
    for n in range(60):
        content, copy = contents[n % len(contents)], (None, "old copy")[n // len(contents) % 2]
        data = {"n": n, "name": "Grüße", "list": [1, 2.5, None], "t": {"x": True}}
        folder = tmp_path / "ours" / str(n) / "sub"
        if content is not None or copy is not None:
            folder.mkdir(parents=True)
        if content is not None:
            (folder / "settings.json").write_text(content, encoding="utf-8")
        if copy is not None:
            (folder / "settings.json.libre-panel.bak").write_text(copy, encoding="utf-8")
        files.append([content, copy, data])
    keys = ['{"key": "abc"}', '{"key": 5}', "{broken", "[]", None, '{"key": ""}']
    for n, content in enumerate(keys):
        folder = tmp_path / "keys" / str(n)
        if content is not None:
            folder.mkdir(parents=True)
            (folder / names["key_file"]).write_text(content, encoding="utf-8")

    [ours] = run([{
        "op": "agents",
        "boards": boards,
        "answers": answers,
        "versions": versions,
        "environment": environment,
        "settings": [[s[0], str(s[1]), *s[2:]] for s in settings],
        "files": [[str(tmp_path / "ours" / str(n) / "sub" / "settings.json"), text(f[2])]
                  for n, f in enumerate(files)],
        "keys": [str(tmp_path / "keys" / str(n)) for n in range(len(keys))],
    }], kept_as=_without_the_keys_made)  # fmt: skip
    assert "boards" in ours, ours

    # (what the boards told: lines of more than one part, plans with usage, costs, sessions
    # listed, tasks hidden, people waited for, notes)
    seen = {"waiting": 0, "line": 0, "quota": 0, "cost": 0, "listed": 0, "hidden": 0, "note": 0}
    for steps, found in zip(boards, ours["boards"], strict=True):
        for step, got in zip(steps, found, strict=True):
            what, rest = step[1], step[2:]
            if what == "status":
                seen["line"] += " · " in got
            elif what == "readings":
                readings = dict(got)
                seen["quota"] += readings["ai.claude.five_hour"] is not None
                seen["cost"] += readings["ai.claude.cost_today"] not in (None, 0, {"float": "0.0"})
                seen["listed"] += readings["ai.claude.s2.state"] is not None
            elif what not in ("hook", "quota", "tokens", "failed", "usage", "heard", "state"):
                named = rest[0]
                seen["waiting"] += len(got["waiting"])
                seen["hidden"] += not named and got["published"][3][1] is None
                seen["note"] += len(got["notes"])
    assert all(count > 20 for count in seen.values()), seen

    seen = {"ok": 0, "old": 0, "tokens": 0, "week": 0}
    for (who, _lines, *_days), got in zip(answers, ours["answers"], strict=True):
        if "err" in got:
            seen["old"] += bool(got["err"][1])
            continue
        seen["ok"] += len(got["ok"]) == 2
        seen["week"] += any(window == "week" for window, *_ in got["ok"])
        if who != "claude":
            tokens = got["tokens"]
            seen["tokens"] += bool(tokens and tokens[0] not in (None, {"float": "0.0"}))
    assert all(count > 20 for count in seen.values()), seen

    for said, got in zip(versions, ours["versions"], strict=True):
        found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", said)
        assert got == (found and [int(part) for part in found.groups()]), said

    seen = {"err": 0, "ours": 0, "line": 0, "other": 0, "port": 0}
    for got in ours["settings"]:
        hooks, status_line, other_status_line, port = got["installed"]
        seen["err"] += "err" in got["with"]
        seen["ours"] += hooks
        seen["line"] += status_line
        seen["other"] += other_status_line
        seen["port"] += port is not None
    assert all(count > 20 for count in seen.values()), seen

    # (the files it wrote, each by its name and digest, kept with its answers; a file that
    # was there is kept beside the new one, once)
    seen = {"new": 0, "copy": 0, "kept": 0, "bad": 0}
    written = []
    for n, ((content, copy, _data), (kept, copied)) in enumerate(
        zip(files, ours["files"], strict=True)
    ):
        folder = tmp_path / "ours" / str(n) / "sub"
        written.append(
            folder.is_dir()
            and sorted(
                [p.name, hashlib.sha256(p.read_bytes()).hexdigest()] for p in folder.iterdir()
            )
        )
        if "err" in kept:
            assert copied is None, content
            seen["bad"] += 1
        elif copied is not None:
            assert Path(copied) == folder / "settings.json.libre-panel.bak"
            seen["copy" if copy is None else "kept"] += 1
        else:
            seen["new"] += 1
    assert all(count > 3 for count in seen.values()), seen
    golden.answered(written)

    for n, ((read, new, again), content) in enumerate(zip(ours["keys"], keys, strict=True)):
        folder = tmp_path / "keys" / str(n)
        assert again == new
        if content == keys[0]:
            assert read == new == "abc"
            continue
        assert read == "" and re.fullmatch(r"[A-Za-z0-9_-]{32}", new), new
        if sys.platform != "win32":
            assert os.stat(folder / names["key_file"]).st_mode & 0o777 == 0o600
        assert [p.name for p in folder.iterdir()] == [names["key_file"]]
    assert len({new for _, new, _ in ours["keys"]}) == len(keys)


@pytest.mark.kept
def test_notices_come_when_notices_py_sends_them() -> None:
    """services/notices.py: the volume, a new track and its cover, the microphone, an
    appointment soon, sensors past their limits (the quiet time, a worse level), the alarm
    and its hold, the panel back from a restart; what is wrong with the options and the
    readings it asks for."""
    from libre_panel import i18n

    limits = [{"key": "cpu.temp", "warn": 70, "crit": 90},
              {"key": "gpu.load", "warn": 80, "max": 200},
              {"key": "x.y", "crit": 50, "name": "Pump", "icon": "fan"},
              {"key": " disk.used ", "crit": 95.5, "icon": 5, "name": 7},
              {"key": "cpu.temp", "crit": 93}, {"key": ""}, 5, {"key": "a", "warn": "x"},
              {"key": "a"}, {"key": "a", "max": True},
              {"key": "a", "warn": 9, "crit": 3},
              {"key": "fan.1", "warn": 2000, "icon": ""}]  # fmt: skip

    def readings_of(rng: random.Random) -> dict[str, list[Any]]:
        pool: dict[str, tuple[list[Any], str, list[str]]] = {
            "audio.volume": ([30, 30.5, 31, 55.0, 55.4, 80, None, "x", True], "%", ["Volume"]),
            "audio.muted": ([0, 0, 1, None], "", ["Muted"]),
            "audio.mic.muted": ([0, 1, 1, 0.0, None, "1"], "", [""]),
            "media.title": (["Song A", "Song A", "Song B", "Größe", None, 5], "", [""]),
            "media.artist": (["Band", "Band", "", None], "", [""]),
            "media.state": (["Playing", "playing", "Playing", "Paused", None], "", [""]),
            "media.playing": ([1, 0, None], "", [""]),
            "media.progress": ([0.25, 0.9, None, "x"], "", [""]),
            "cpu.temp": ([60, 75, 75.25, 92, 95, None], "°C", ["CPU temperature", ""]),
            "gpu.load": ([50, 85, 210, 9.96], "%", ["GPU load"]),
            "x.y": ([40, 55, 4.25, 50], "l/h", ["Flow", ""]),
            "disk.used": ([96, 9.5, 95.5], "%", ["Disk"]),
            "fan.1": ([1500, 2400], "RPM", ["Fan 1"]),
        }
        for n in (1, 2, 3):
            pool[f"calendar.{n}.title"] = (["Standup", "Lunch", "Lunch", None, 5], "", [""])
            pool[f"calendar.{n}.minutes"] = ([5, 0.2, 0.5, 0.49, 12, -1, 2.5, 1.5, None], "", [""])
            pool[f"calendar.{n}.start"] = ([1.78e9, 1.78e9 + 600, 1780000300, None], "", [""])
            pool[f"calendar.{n}.time"] = (["09:30", "09:30", i18n.t("All day"), "", None], "", [""])
        return {key: [rng.choice(values), unit, rng.choice(labels)]
                for key, (values, unit, labels) in pool.items() if rng.random() < 0.75}  # fmt: skip

    defaults = golden.given("notices_defaults")  # (the service's options, each as it is at first)
    for language in ("en", "de"):
        i18n.set_language(language)
        try:
            rng = random.Random(0x4071CE)
            runs = []
            for _ in range(250):
                options = {**defaults, "warnings": rng.sample(limits, rng.randrange(5)),
                           "volume_look": rng.choice(["", "", "ring", "nope"]),
                           "appointments": rng.choice([10, 10, 0, 30]),
                           "quiet_s": rng.choice([120.0, 0, 5])}  # fmt: skip
                for name in ("volume", "music", "microphone", "restarted", "alarm"):
                    options[name] = rng.random() < 0.85
                began = rng.uniform(0, 1000)
                now, steps = began, []
                for _ in range(rng.randrange(1, 60)):
                    now += rng.choice([0.05, 0.3, 1, 1, 1, 2.5, 5, 12, 40, 130])
                    kind = rng.choice(["sample"] * 7 + ["fresh"] * 2 + ["restarted"])
                    pictures = [key for key in ("media.cover", "media.app_icon")
                                if rng.random() < 0.4]  # fmt: skip
                    more: Any = "14:05"
                    if kind == "fresh":
                        more = rng.sample(["audio.volume", "audio.muted", "media.title",
                                           "media.state", "audio.mic.muted", "cpu.temp"],
                                          rng.randrange(1, 4))  # fmt: skip
                    steps.append([now, kind, json.dumps(readings_of(rng)), pictures, more])
                runs.append([json.dumps(options), began, steps])
            [ours] = run([{"op": "notices", "locale": str(i18n.LOCALE_DIR), "language": language,
                           "runs": runs}])  # fmt: skip
            assert "runs" in ours, ours

            seen = {kind: 0 for kind in ("volume", "music", "microphone", "appointment",
                                         "warning", "panel", "publish", "unpublish", "note",
                                         "error", "report")}  # fmt: skip
            for (_options, _began, steps), got in zip(runs, ours["runs"], strict=True):
                seen["report"] += len(got["reports"])
                for (_now, kind, *_), found in zip(steps, got["steps"], strict=True):
                    if kind == "fresh" and not got["follows"]:
                        continue  # (not listened for: the loop does not tell it)
                    # (what it did: told a message (its text, icon, level, seconds, kind, ...),
                    # published or unpublished the alarm, wrote a note)
                    for act in found:
                        seen[act[5] if act[0] == "tell" else act[0]] += 1
                        seen["error"] += act[0] == "tell" and act[3] == "error"
            assert all(count > 20 for count in seen.values()), seen
        finally:
            i18n.set_language("en")


def test_paths_are_taken_as_os_path_and_pathlib_take_them() -> None:
    """lp-services' paths: splitroot, normpath, normcase and join of ntpath and posixpath,
    and what pathlib makes of a path (its text, name, suffix, anchor, parts; joined with
    another), for Windows' paths and the others' on every system."""
    import ntpath
    import posixpath
    import sys
    from pathlib import PurePosixPath, PureWindowsPath

    rng = random.Random(0x9A785)
    pieces = [
        "",
        ".",
        "..",
        "a",
        "Games",
        "Steam Apps",
        "x.exe",
        ".hidden",
        "b.tar.gz",
        "C:",
        "c:",
        "D:",
        "$",
        "?",
        "UNC",
        "unc",
        "server",
        "share",
        "Ä ö",
        "end.",
        " ",
    ]
    seps = ["/", "/", "\\", "\\", "//", "\\\\", "/./", "\\..\\"]

    def made() -> str:
        text = rng.choice(["", "", "/", "\\", "//", "\\\\", "C:", "c:\\", "D:/", "\\\\?\\",
                           "//?/UNC/", "\\\\.\\", "///", ' "'])  # fmt: skip
        for _ in range(rng.randrange(6)):
            text += rng.choice(pieces) + rng.choice(seps + [""])
        return text

    rows = [[rng.random() < 0.5, made(), made()] for _ in range(6000)]
    marks = [
        "~",
        "~/",
        "~root",
        "~nobody-such",
        "~me",
        "~you\\",
        "$HOME",
        "${HOME}",
        "$NONE",
        "${NONE}",
        "%USERPROFILE%",
        "%NONE%",
        "%",
        "$$",
        "%%",
        "'",
        "$A-B",
        "${A B}",
        "$",
    ]
    for _ in range(3000):
        text = "".join(rng.choice(marks + pieces + seps) for _ in range(rng.randrange(1, 6)))
        rows.append([rng.random() < 0.5, text, ""])
    envs = [{"HOME": "/home/me/", "USERPROFILE": "C:\\Users\\me", "USERNAME": "me", "A": "1",
             "A-B": "dash", "A B": "space"},
            {"HOMEPATH": "\\Users\\you", "HOMEDRIVE": "D:", "USERNAME": "you"},
            {"HOMEPATH": "home", "USERNAME": "other", "HOME": ""}, {}]  # fmt: skip
    rows = [[*row, rng.randrange(len(envs))] for row in rows]
    answers = [run([{"op": "service_paths", "env": env,
                     "paths": [row[:3] for row in rows if row[3] == n]}])[0]["paths"]
               for n, env in enumerate(envs)]  # fmt: skip
    places = [0] * len(envs)
    ours = {"paths": []}
    for row in rows:
        ours["paths"].append(answers[row[3]][places[row[3]]])
        places[row[3]] += 1
    rows_env = [envs[row[3]] for row in rows]
    rows = [row[:3] for row in rows]
    import os

    seen = {"drive": 0, "unc": 0, "back": 0, "absolute": 0, "replaced": 0, "home": 0, "set": 0}
    real = os.environ
    for (windows, path, more), env, got in zip(rows, rows_env, ours["paths"], strict=True):
        flavour, pure = (ntpath, PureWindowsPath) if windows else (posixpath, PurePosixPath)
        whole = pure(path)
        if windows and sys.platform != "win32":
            folded = ntpath.normpath(path).replace("/", "\\").lower()
        else:
            folded = flavour.normcase(flavour.normpath(path))
        want = [
            list(flavour.splitroot(path)), flavour.normpath(path), folded,
            flavour.join(path, more),
            [str(whole), whole.name, whole.suffix, whole.anchor, whole.is_absolute(),
             str(whole / more), list(whole.parts[1:] if whole.anchor else whole.parts)],
            path.strip().strip('"'),
        ]  # fmt: skip
        os.environ = env  # type: ignore[assignment]  # noqa: B003 - ntpath reads it itself
        try:
            want += [flavour.expanduser(path), flavour.expandvars(path)]
        finally:
            os.environ = real  # noqa: B003
        assert got == want, (windows, path, more, env)
        seen["home"] += want[-2] != path
        seen["set"] += want[-1] != path
        seen["drive"] += bool(windows and whole.drive[1:] == ":")
        seen["unc"] += whole.drive.startswith("\\\\")
        seen["back"] += ".." in path and ".." not in flavour.normpath(path)
        seen["absolute"] += whole.is_absolute()
        seen["replaced"] += bool(more) and str(whole / more) == str(pure(more))
    assert all(count > 100 for count in seen.values()), seen


def _vdf(table: dict[str, Any], depth: int = 0) -> str:
    """Steam's text format as Steam writes it: a backslash and a quote escaped (a Windows path's
    backslashes doubled)."""

    def quoted(text: Any) -> str:
        return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'

    tabs = "\t" * depth
    lines = []
    for key, value in table.items():
        if isinstance(value, dict):
            lines += [f"{tabs}{quoted(key)}", tabs + "{", _vdf(value, depth + 1), tabs + "}"]
        else:
            lines.append(f"{tabs}{quoted(key)}\t\t{quoted(value)}")
    return "\n".join(lines)


@pytest.mark.kept
def test_steams_games_and_logos_are_found_as_steam_py_finds_them(tmp_path: Any) -> None:
    """services/steam.py: Steam's text format, its folder (named, in the registry, under the
    program folders or the home folder), its libraries and the games in their manifests
    without the tools and those ignored, the game of a program's path, and a game's logo
    from the cache (the newest, cut out; else the header picture)."""
    import os
    from pathlib import Path

    from PIL import Image

    rng = random.Random(0x57EA)
    words = [
        '"a"',
        '"B c"',
        '"x\\ty"',
        '"q\\"r"',
        '"n\\\\"',
        "{",
        "}",
        "{",
        "}",
        "// note",
        "\n",
        "\t",
        " ",
        '"',
        "\\",
        "bare",
        '"Ä"',
        '"1"',
        '"path"',
        '"\\\n"',
        "/",
        "\r\n",
    ]
    texts = ["".join(rng.choice(words) + rng.choice(["", " ", "\n"])
                     for _ in range(rng.randrange(40))) for _ in range(1500)]  # fmt: skip
    texts += [_vdf({"AppState": {"appid": "440", "Name": "Team ™ Fortress", "x": {"Y": {}}}})]

    def picture(path: Path, size: tuple[int, int], box: Any, when: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".png":
            made = Image.new("RGBA", size, (0, 0, 0, 0))
            if box:
                made.paste((200, 40, rng.randrange(256), 255), box)
        else:
            made = Image.new("RGB", size, (rng.randrange(256), 90, 30))
        made.save(path)
        os.utime(path, (when, when))

    tools = ["Proton 9.0", "Steamworks Shared", "SteamLinuxRuntime_sniper", "wallpaper_engine"]
    titles = ["Half-Life™ 2", "Portal  2 ®", "Dota 2", "Größe", "Ignored Game", "Stray©"]
    libraries_ = []
    for n in range(120):
        base = tmp_path / str(n)
        kind = rng.choice(["home", "home", "given", "wrong", "registry", "folders", "none"])
        root = base / "home" / ".local" / "share" / "Steam"
        if kind == "folders":
            root = base / "pf" / "Steam"
        other = base / "D" / "SteamLibrary"
        places = [root] + ([other] if rng.random() < 0.7 else [])
        apps = {}
        for library in places:
            (library / "steamapps" / "common").mkdir(parents=True)
            for _ in range(rng.randrange(5)):
                appid = str(rng.randrange(1, 4000))
                title = rng.choice(titles + tools)
                folder = rng.choice([title, title.replace(" ", "_"), f"game{appid}"])
                state = {"appid": appid, "name": title, "installdir": folder}
                drop = rng.choice([None, None, None, "name", "installdir", "appid"])
                state.pop(drop, None)
                name = rng.choice(
                    [f"appmanifest_{appid}.acf"] * 6
                    + [f"Appmanifest_{appid}.acf", f"appmanifest_{appid}.txt"]
                )
                text = _vdf({rng.choice(["AppState", "appstate"]): state})
                if rng.random() < 0.08:
                    text = rng.choice(["", '"AppState" "none"', "{{{", text[: len(text) // 2]])
                (library / "steamapps" / name).write_text(text, encoding="utf-8")
                apps[appid] = library / "steamapps" / "common" / folder
            if rng.random() < 0.2:
                (library / "steamapps" / "appmanifest_dir.acf").mkdir()
        listed: dict[str, Any] = {"contentstatsid": "77", "0": {"path": str(root)}}
        if rng.random() < 0.8:
            listed["1"] = rng.choice(
                [
                    {"path": str(other)},
                    str(other),
                    {"path": str(other) + "/"},
                    {"path": f"{root}/../Steam"},
                    {"path": "relative/lib"},
                    {"label": "none"},
                    {"path": str(base / "missing")},
                ]
            )
        listed["x2"] = str(other)
        (root / "steamapps" / "libraryfolders.vdf").write_text(
            _vdf({"libraryfolders": listed}), encoding="utf-8"
        )
        cache = root / "appcache" / "librarycache"
        logos = sorted(apps)[:3] + ["12x"]
        for appid in logos:
            when = 1.7e9
            for where in rng.sample([f"{appid}/aaa/logo.png", f"{appid}/bbb/logo.png",
                                     f"{appid}/logo.png", f"{appid}_logo.png",
                                     f"{appid}/ccc/header.jpg", f"{appid}_header.jpg",
                                     f"{appid}/header.png"], rng.randrange(6)):  # fmt: skip
                when += rng.choice([-500, 300, 900])
                box = rng.choice([(5, 4, 30, 15), (0, 0, 40, 20), None])
                picture(cache / where, (40, 20), box, when)
                if rng.random() < 0.15:
                    (cache / where).write_bytes(b"no picture")
                    os.utime(cache / where, (when, when))
        case: dict[str, Any] = {
            "given": {"given": str(root), "wrong": str(base / "D")}.get(kind, ""),
            "ignore": json.dumps(rng.choice([[], ["ignored game", " Dota 2 "], [5, "", "game7"]])),
            "windows": kind in ("registry", "folders"),
            "home": str(base / ("home" if kind == "home" else "elsewhere")),
            "env": {}, "registry": [],
        }  # fmt: skip
        if kind == "registry":
            case["registry"] = [["HKCU", "own", r"Software\Valve\Steam",
                                 {"SteamPath": str(root).replace(os.sep, "/")}, []]]  # fmt: skip
        if kind == "folders":
            case["env"] = {"ProgramFiles(x86)": str(base / "nowhere"),
                           "ProgramFiles": str(base / "pf")}  # fmt: skip
        now, steps = rng.uniform(0, 500), []
        for _ in range(rng.randrange(4, 30)):
            now += rng.choice([0, 1, 30, 130])
            what = rng.choice(["games", "root", "match", "match", "match", "logo", "logo"])
            if what == "match":
                folder = rng.choice(
                    [
                        *apps.values(),
                        root / "steamapps" / "common",
                        base / "D" / "x",
                        other / "steamapps" / "common" / "none",
                    ]
                )
                path = rng.choice([str(folder / "bin" / "game.exe"), str(folder), "",
                                   str(folder / ".." / folder.name / "a.exe"), str(folder).upper(),
                                   str(folder) + "/"])  # fmt: skip
                steps.append([now, what, path])
            elif what == "logo":
                steps.append([now, what, rng.choice([*logos, *apps, "1"])])
            else:
                steps.append([now, what])
        libraries_.append(case | {"steps": steps})

    [ours] = run([{"op": "steam", "vdf": texts, "libraries": libraries_}])
    assert len(ours["vdf"]) == len(texts)
    seen = {"games": 0, "two": 0, "match": 0, "cut": 0, "note": 0, "none": 0}
    for case, got in zip(libraries_, ours["libraries"], strict=True):
        for (_now, what, *_more), found in zip(case["steps"], got["steps"], strict=True):
            if what == "games":  # (each its number, its name and its folder)
                seen["games"] += len(found)
                seen["two"] += len({game[2].split("steamapps")[0] for game in found}) > 1
            elif what == "match":  # (the game of a program's path)
                seen["match"] += found is not None
            elif what == "root":  # (Steam's folder)
                seen["none"] += found is None
            else:  # (a game's logo: its size and its pixels' digest)
                seen["cut"] += bool(found) and found[:2] != [40, 20]
        seen["note"] += len(got["notes"])
    assert all(count > 5 for count in seen.values()), seen


@pytest.mark.kept
def test_the_launchers_games_are_found_as_launchers_py_finds_them(
    tmp_path: Any,
) -> None:
    """services/launchers.py: Epic's manifests, GOG, EA, Battle.net, Rockstar and Ubisoft
    from the registry, Amazon's and Lutris's databases, the Xbox app's folders, Heroic's
    lists and the folders named; a catalog of them (the innermost folder's game, folders too
    wide, names ignored, the launchers off, a list that is odd)."""
    import os
    import sqlite3
    from pathlib import Path

    rng = random.Random(0x1A04C)
    # (the registry's keys the launchers' games are listed under, as launchers.py names them)
    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    ubisoft = r"SOFTWARE\Ubisoft\Launcher\Installs"

    def folder(path: Path, *programs: str) -> str:
        path.mkdir(parents=True, exist_ok=True)
        for name in programs:
            (path / name).write_bytes(b"MZ")
        return str(path)

    def write(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = value if isinstance(value, str) else json.dumps(value)
        path.write_bytes((b"\xef\xbb\xbf" if rng.random() < 0.3 else b"") + text.encode())

    def database(path: Path, table: str, columns: str, rows: list[tuple[Any, ...]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as made:
            made.execute(f"CREATE TABLE {table} ({columns})")
            made.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)
        made.close()

    odd = [[1], 5, "x", None, {"InstallationList": None}, {"InstallationList": 7}]
    computers = []
    for n in range(140):
        base = tmp_path / str(n)
        home, games = base / "home" / "me", base / "Games"
        windows = rng.random() < 0.5
        env: dict[str, str] = {"GAMES": str(games), "HOME": str(home), "USERPROFILE": str(home)}
        registry: list[list[Any]] = []
        drives: list[str] = []
        if windows:
            programdata, local, roaming = base / "ProgramData", base / "Local", base / "Roaming"
            env |= {"PROGRAMDATA": str(programdata), "LOCALAPPDATA": str(local),
                    "APPDATA": str(roaming)}  # fmt: skip
            if rng.random() < 0.1:
                del env["APPDATA"]
                roaming = home / "AppData" / "Roaming"
            epic = programdata / "Epic"
            listed = [
                {"AppName": "Fortnite", "InstallLocation": folder(games / "Fortnite")},
                {"AppName": None, "InstallLocation": ""},
                "x",
                {"InstallLocation": 5},
            ]
            kind = rng.random()
            if kind < 0.7:
                write(
                    epic / "UnrealEngineLauncher" / "LauncherInstalled.dat",
                    {"InstallationList": rng.choice([listed, listed, [], "abc", {"a": 1}])},
                )
            elif kind < 0.8:
                write(epic / "UnrealEngineLauncher" / "LauncherInstalled.dat", rng.choice(odd))
            for m in range(rng.randrange(5)):
                app = rng.choice(["Fortnite", "Rocket", "ue plugin", "Addon", "", None])
                where = rng.choice([folder(games / f"epic{m}", "game.exe"), str(games / "gone"),
                                    "", None, f' "{folder(games / "quoted")}" '])  # fmt: skip
                item: dict[str, Any] = {
                    "AppName": app,
                    "InstallLocation": where,
                    "DisplayName": rng.choice(["Rocket League®", "", None, 5]),
                }
                item["AppCategories"] = rng.choice([
                    ["public", "games"], ["addons"], ["addons", "addons/launchable"], ["plugins"],
                    None, "games", "addons", {"plugins/engine": 1}, [], ["games", 5]])  # fmt: skip
                if rng.random() < 0.3:
                    item["CompatibleApps"] = rng.choice([["UE_5.3"], ["x"], [5], None, "UE_", {}])
                if rng.random() < 0.2:
                    item["TechnicalType"] = rng.choice(["games", "plugins/engine,x", None])
                if rng.random() < 0.04:
                    item[rng.choice(["AppCategories", "CompatibleApps", "InstallLocation"])] = 7
                name = rng.choice([f"m{m}.item", f"M{m}.ITEM", f"m{m}.json", f".m{m}.item"])
                write(epic / "EpicGamesLauncher" / "Data" / "Manifests" / name,
                      rng.choice([item] * 8 + [[item], "{broken"]))  # fmt: skip
            programs: dict[str, dict[str, dict[str, Any]]] = {"wide": {}, "narrow": {}, "own": {}}
            for _ in range(rng.randrange(9)):
                view = rng.choice(["wide", "wide", "narrow", "own"])
                number = str(rng.randrange(1000, 1020))
                place = folder(games / f"prog{number}")
                kind_of = rng.choice(["gog", "gog", "ea", "bnet", "rockstar", "uplay", "other"])
                if kind_of == "gog":
                    key = rng.choice([f"{number}_is1", f"{number}_is1", f"x{number}_is1", "_is1"])
                    values = {"DisplayName": rng.choice(["Witcher  3", ""]),
                              "Publisher": rng.choice(["GOG.com", "GOG.com", "gog"]),
                              "InstallLocation": rng.choice([place, place, ""])}  # fmt: skip
                    if rng.random() < 0.5:
                        root = rng.choice([number, "", None, "999", 999, int(number)])
                        write(
                            Path(place) / f"goggame-{number}.info",
                            rng.choice([{"rootGameId": root}, {"rootGameId": root}, {}, [1]]),
                        )
                elif kind_of == "ea":
                    key = f"{{EA-{number}}}"
                    values = {"DisplayName": rng.choice(["Battlefield™", "EA app", "Origin Helper",
                                                         "EA AntiCheat"]),
                              "Publisher": "Electronic Arts", "InstallLocation": place}  # fmt: skip
                elif kind_of == "bnet":
                    key = rng.choice(["World of Warcraft", "Battle.net", "Diablo IV Beta"])
                    uid = rng.choice(["wow", "battle.net", "fenris", "WoW_classic"])
                    line = rng.choice(
                        [
                            f'"C:\\PF\\Battle.net\\Battle.net.exe" --lang=enUS --uid={uid} '
                            '--displayname="x"',
                            f"battle.NET --UID={uid}",
                            f"Battle.net.exe --uid=a --uid={uid}",
                            "Battle.net.exe",
                            f"x\nBattle.net --uid={uid}",
                        ]
                    )
                    values = {"DisplayName": key, "InstallLocation": rng.choice([place, place, ""]),
                              "UninstallString": line}  # fmt: skip
                elif kind_of == "rockstar":
                    key = f"{{R-{number}}}"
                    title = rng.choice(["gta5", "GTA5", "rdr2", "launcher", ' "bully" '])
                    line = rng.choice(
                        [
                            f'"C:\\R\\Launcher.exe" -enableFullMode -uninstall={title}',
                            f"UNINSTALL.EXE x uninstall={title}",
                            f"uninstall={title}",
                        ]
                    )
                    values = {"DisplayName": "Grand Theft Auto V", "InstallLocation": place,
                              "UninstallString": line}  # fmt: skip
                elif kind_of == "uplay":
                    key = f"Uplay Install {number}"
                    values = {"DisplayName": rng.choice(["Anno 1800", ""])}
                else:
                    key, values = f"prog{number}", {"DisplayName": "Tool", "InstallLocation": place,
                                                    "Size": 5, "Zero": 0}  # fmt: skip
                programs[view][key] = values
            for view, entries in programs.items():
                hive = "HKCU" if view == "own" else "HKLM"
                if entries or rng.random() < 0.5:
                    registry.append([hive, view, uninstall, {}, list(entries)])
                registry += [[hive, view, f"{uninstall}\\{key}", values, []]
                             for key, values in entries.items()]  # fmt: skip
            for view in ("wide", "narrow"):
                numbers = [str(rng.randrange(1000, 1020)) for _ in range(rng.randrange(3))]
                if numbers:
                    registry.append(["HKLM", view, ubisoft, {}, numbers])
                for number in dict.fromkeys(numbers):
                    place = folder(games / f"ubi{number}").replace(os.sep, "/")
                    values = {"InstallDir": rng.choice([place + "/", place, ""])}
                    registry.append(["HKLM", view, f"{ubisoft}\\{number}", values, []])
            if rng.random() < 0.5:
                database(
                    local / "Amazon Games" / "Data" / "Games" / "Sql" / "GameInstallInfo.sqlite",
                    "DbSet",
                    "Id, ProductTitle, InstallDirectory, Installed",
                    [
                        ("amzn1", "New World", folder(games / "NewWorld"), 1),
                        ("amzn2", None, folder(games / "Lost Ark"), 1),
                        ("amzn3", "Gone", folder(games / "gone3"), 0),
                        (5, 7.5, "", 1),
                    ],
                )
            drive = base / "X"
            for name in rng.sample(["Forza", "Halo", ".hidden", "file.txt"], rng.randrange(4)):
                (
                    folder(drive / "XboxGames")
                    and name != "file.txt"
                    and folder(drive / "XboxGames" / name)
                )
                if name == "file.txt":
                    (drive / "XboxGames" / name).write_text("x")
            drives = [str(drive), str(base / "nodrive")]
            heroic = roaming / "heroic"
        else:
            config = home / ".config"
            if rng.random() < 0.4:
                config = base / "xdg"
                env["XDG_CONFIG_HOME"] = str(config)
            if rng.random() < 0.2:
                env["XDG_DATA_HOME"] = rng.choice(["", str(base / "data")])
            data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
            if rng.random() < 0.6:
                database(
                    rng.choice([data, home / ".var/app/net.lutris.Lutris/data"])
                    / "lutris"
                    / "pga.db",
                    "games",
                    "slug, name, directory, runner, installed",
                    [
                        ("celeste", "Celeste", folder(games / "celeste"), "linux", 1),
                        (None, None, folder(games / "noname"), "wine", 1),
                        ("cs", "CS", folder(games / "cs"), "steam", 1),
                        ("gone", "Gone", folder(games / "gone4"), "wine", 0),
                        ("nodir", "No dir", "", "wine", 1),
                        ("null", "Null", None, None, 1),
                    ],
                )
            heroic = rng.choice(
                [config / "heroic", home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"]
            )
        if rng.random() < 0.6:
            write(
                heroic / "legendaryConfig" / "legendary" / "installed.json",
                rng.choice(
                    [
                        {
                            "Sugar": {
                                "title": "Rocket League",
                                "install_path": folder(games / "rl"),
                            },
                            "Dlc": {
                                "title": "DLC",
                                "install_path": folder(games / "rl"),
                                "is_dlc": True,
                            },
                            "Odd": 5,
                            "None": {"title": None, "install_path": None},
                        },
                        [1],
                        "{broken",
                    ]
                ),
            )
        if rng.random() < 0.6:
            write(
                heroic / "gog_store" / "installed.json",
                rng.choice(
                    [
                        {
                            "installed": [
                                {"appName": "1207", "install_path": folder(games / "gog1")},
                                {"install_path": folder(games / "gog2")},
                                5,
                                {
                                    "appName": "9",
                                    "install_path": folder(games / "gog1"),
                                    "is_dlc": 1,
                                },
                            ]
                        },
                        {"installed": rng.choice([None, 5, "abc", {"a": 1}, []])},
                        {},
                        [1],
                    ]
                ),
            )
        if rng.random() < 0.6:
            write(
                heroic / "nile_config" / "nile" / "installed.json",
                rng.choice(
                    [
                        [
                            {"id": "amzn-1", "path": folder(games / "nile1")},
                            {"path": folder(games / "nile2")},
                            {"id": "none"},
                            "x",
                        ],
                        {"id": 1},
                    ]
                ),
            )
        mine = base / "MyGames"
        folder(mine / "Own Game", "run.EXE")
        folder(mine / "Pack" / "Inner One", "a.sh")
        folder(mine / "Pack" / "Inner Two")
        folder(mine / "Pack" / ".cache")
        (mine / "Pack" / "readme.txt").write_text("x")
        folder(home, *(["start.sh"] if rng.random() < 0.3 else []))
        folder(home / "Homegame")
        named = rng.sample(
            [
                str(mine / "Own Game"),
                str(mine / "Pack"),
                f' "{mine / "Pack"}" ',
                str(base / "missing"),
                "",
                5,
                "$GAMES",
                "${GAMES}/rl",
                "~",
                "~/Homegame",
                str(games),
            ],
            rng.randrange(5),
        )
        ignore = rng.choice([[], [], ["inner one", " ROCKET LEAGUE "], ["celeste", 5, ""]])
        now, steps = rng.uniform(0, 300), []
        # (in the order of their names: the folder's own is the file system's)
        spots = [games / name for name in sorted(os.listdir(games))] if games.is_dir() else []
        spots += [mine / "Own Game", mine / "Pack" / "Inner One", mine / "Pack", home, base]
        for _ in range(rng.randrange(3, 25)):
            now += rng.choice([0, 1, 60, 130])
            if rng.random() < 0.3:
                steps.append([now, "games"])
            else:
                spot = rng.choice(spots)
                steps.append(
                    [
                        now,
                        "match",
                        rng.choice(
                            [
                                str(spot / "bin" / "x.exe"),
                                str(spot),
                                "",
                                str(spot) + "x",
                                str(spot / ".." / spot.name),
                            ]
                        ),
                    ]
                )
        computers.append(
            {
                "windows": windows,
                "home": str(home),
                "env": env,
                "registry": registry,
                "drives": drives,
                "named": json.dumps(named),
                "ignore": json.dumps(ignore),
                "launchers": rng.random() < 0.85,
                "steps": steps,
            }
        )

    [ours] = run([{"op": "launchers", "computers": computers}])
    assert "computers" in ours, ours

    seen = {
        key: 0
        for key in (
            "epic",
            "gog",
            "ea",
            "battlenet",
            "rockstar",
            "ubisoft",
            "amazon",
            "xbox",
            "lutris",
            "folder",
            "odd",
            "match",
            "note",
            "ignored",
        )
    }
    # (a game: its launcher, its key, its name, its folder)
    for case, got in zip(computers, ours["computers"], strict=True):
        if got["read"] == "odd":
            seen["odd"] += 1
        else:
            for game in got["read"]:
                seen[game[0]] += 1
        for (_now, what, *_more), found in zip(case["steps"], got["steps"], strict=True):
            if what == "games":
                seen["folder"] += sum(game[0] == "folder" for game in found)
                seen["ignored"] += bool(json.loads(case["ignore"])) and len(found) > 0
            else:
                seen["match"] += found is not None
        seen["note"] += len(got["notes"])
    assert all(count > 5 for count in seen.values()), seen


@pytest.mark.live  # (this computer's own programs, as they run while it looks)
def test_the_computers_programs_are_those_machine_py_names(tmp_path: Any) -> None:
    """The programs that run (their names, where they lie; a Windows program under Wine by
    its process's name and the folder it works in; a name the kernel cut), how long one has
    run, how long the system has been up; and whether the registry says a program records:
    of programs the test starts itself, as the Python app's services/machine.py named them."""
    import os
    import shutil
    import subprocess
    import sys
    import time

    spawned = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])]
    special = {}
    # (copies of a program that waits under any name of its own: bash, by its own read (sleep
    # can be one program of many, a multicall coreutils that ends at once under another name))
    bash = shutil.which("bash")
    if sys.platform.startswith("linux") and bash:
        for name in ("wine-preloader-x", "A-Very-Long-Program-Name.x86_64", "Short"):
            copy = tmp_path / name
            shutil.copy(bash, copy)
            work = tmp_path / f"work {name}"
            work.mkdir()
            spawned.append(subprocess.Popen([str(copy), "-c", "read -t 60"], cwd=work,
                                            stdin=subprocess.PIPE))  # fmt: skip
            special[name] = spawned[-1].pid
    key = (  # (where Windows keeps which programs used the microphone)
        r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    )
    microphones = [
        [],
        [
            ["HKCU", "own", key, {}, ["App_1", "NonPackaged"]],
            ["HKCU", "own", f"{key}\\App_1", {"LastUsedTimeStart": 5, "LastUsedTimeStop": 9}, []],
            ["HKCU", "own", f"{key}\\NonPackaged", {}, ["C:#x.exe", "C:#y.exe"]],
            ["HKCU", "own", f"{key}\\NonPackaged\\C:#x.exe", {"LastUsedTimeStart": 5}, []],
            [
                "HKCU",
                "own",
                f"{key}\\NonPackaged\\C:#y.exe",
                {"LastUsedTimeStart": 7, "LastUsedTimeStop": 0},
                [],
            ],
        ],
        [
            ["HKCU", "own", key, {}, ["App_1", "App_2", "Gone"]],
            ["HKCU", "own", f"{key}\\App_1", {"LastUsedTimeStart": 0, "LastUsedTimeStop": 0}, []],
            ["HKCU", "own", f"{key}\\App_2", {"LastUsedTimeStart": 3, "LastUsedTimeStop": 0}, []],
        ],
        [
            ["HKCU", "own", key, {}, ["NonPackaged", "App"]],
            [
                "HKCU",
                "own",
                f"{key}\\NonPackaged",
                {"LastUsedTimeStart": 3, "LastUsedTimeStop": 0},
                [],
            ],
            ["HKCU", "own", f"{key}\\App", {"LastUsedTimeStart": 3, "LastUsedTimeStop": 4}, []],
        ],
        [
            ["HKLM", "own", key, {}, ["App"]],
            ["HKLM", "own", f"{key}\\App", {"LastUsedTimeStart": 3, "LastUsedTimeStop": 0}, []],
        ],
    ]
    try:
        time.sleep(0.5)  # (the programs are there, under their own names)
        pids = [os.getpid(), *(child.pid for child in spawned), 2**30]
        [ours] = run([{"op": "machine", "pids": pids, "microphones": microphones}])
    finally:
        for child in spawned:
            child.kill()
            child.wait()
            if child.stdin:
                child.stdin.close()
    got = {pid: (name, path) for name, pid, path in ours["programs"]}
    assert len(got) == len(ours["programs"]) and len(got) > 3
    assert [pid for _, pid, _ in ours["programs"]] == sorted(got)
    # (the test itself and the program it started: Python, where it lies)
    for pid in pids[:2]:
        name, path = got[pid]
        assert name and os.path.basename(path).lower().startswith(("python", "pytest")), got[pid]
    if special:
        work = str(tmp_path / "work wine-preloader-x")
        assert got[special["wine-preloader-x"]] == ("wine-preloader-x", work)
        long = "a-very-long-program-name.x86_64"
        assert got[special[long.replace("a-very-long-p", "A-Very-Long-P")
                           .replace("rogram-name", "rogram-Name")]][0] == long  # fmt: skip
        assert got[special["Short"]] == ("short", str(tmp_path / "Short"))
    # (how long each has run: the test longer than the programs it started, none of them
    # long; a program that is none; the system up for longer than any of them)
    assert all(seconds is not None for seconds in ours["running"][:-1])
    assert ours["running"][-1] is None and ours["running"][1] < 30
    assert ours["running"][0] >= ours["running"][1] and ours["uptime"] >= ours["running"][0]
    assert isinstance(ours["front"][0], bool)
    assert ours["front"][1] is None or isinstance(ours["front"][1], int)
    assert ours["microphones"] == [False, True, True, False, False]


@pytest.mark.kept
def test_game_mode_follows_the_games_as_game_py_follows_them(tmp_path: Any) -> None:
    """services/game.py: a game found by the list of programs, by Steam's library or by a
    folder named (with a window in front once, where that can be told), kept for a while
    after its program ended; the name, the logo and the time played it publishes, the mode
    it switches to and back (waiting for a sample with the logo, with the entrance or a
    message), and how long its worker sleeps."""
    from fake_system import make_library
    from PIL import Image

    from libre_panel import i18n

    root = make_library(tmp_path / "Steam", ("440", "Team Fortress 2", "Team Fortress 2"),
                        ("620", "Portal 2", "Portal 2"), ("1", "Proton 9", "Proton 9"))  # fmt: skip
    cache = root / "appcache" / "librarycache"
    (cache / "440").mkdir(parents=True)
    logo = Image.new("RGBA", (30, 12), (0, 0, 0, 0))
    logo.paste((250, 180, 20, 255), (4, 2, 26, 10))
    logo.save(cache / "440" / "logo.png")
    mine = tmp_path / "MyGames"
    (mine / "Celeste").mkdir(parents=True)
    (mine / "Hades").mkdir()
    common = root / "steamapps" / "common"
    places = [("hl2.exe", common / "Team Fortress 2" / "hl2.exe"),
              ("launcher.exe", common / "Team Fortress 2" / "bin" / "launcher.exe"),
              ("portal2.exe", common / "Portal 2" / "portal2.exe"),
              ("proton", common / "Proton 9" / "proton"),
              ("celeste.exe", mine / "Celeste" / "Celeste.exe"),
              ("hades", mine / "Hades" / "x64" / "Hades"),
              ("game.exe", tmp_path / "elsewhere" / "game.exe"), ("tool.exe", ""),
              ("notepad.exe", tmp_path / "Windows" / "notepad.exe")]  # fmt: skip
    defaults = golden.given("game_defaults")  # (the service's options, each as it is at first)

    for language in ("en", "de"):
        i18n.set_language(language)
        try:
            rng = random.Random(0x6A3E)
            services = []
            for _ in range(250):
                options = defaults | {
                    "programs": rng.choice([[], ["game.exe", "Tool.EXE = My Tool"],
                                            ["tool.exe", 5, " = x", "hades=", "Portal2.exe"]]),
                    "steam": rng.random() < 0.8,
                    "steam_path": rng.choice([str(root)] * 5 + [str(tmp_path / "nowhere")]),
                    "launchers": False,
                    "folders": rng.choice([[], [str(mine)], [str(mine)]]),
                    "ignore": rng.choice([[], [], ["portal 2"], ["celeste"]]),
                    "automatic": rng.random() < 0.9, "force": rng.random() < 0.1,
                    "entrance": rng.random() < 0.6, "logo": rng.random() < 0.8,
                    "linger_s": rng.choice([6.0, 0, 2]), "poll_s": rng.choice([2.0, 0.1, 100]),
                }  # fmt: skip
                pids = {name: 100 + n for n, (name, _) in enumerate(places)}
                now, steps = rng.uniform(0, 500), []
                running: list[str] = []
                for _ in range(rng.randrange(2, 50)):
                    now += rng.choice([0.05, 0.3, 0.3, 0.7, 1.0, 1.5, 3.0, 8.0])
                    what = rng.choice(
                        ["detect"] * 5 + ["tick"] * 2 + ["advance"] * 2 + ["sample"] * 4
                    )
                    if what == "detect":
                        if rng.random() < 0.4:
                            running = rng.sample([name for name, _ in places], rng.randrange(5))
                        if rng.random() < 0.1:
                            pids[rng.choice(list(pids))] += 50  # (it started anew)
                        rows = [[name, pids[name], str(dict(places)[name])] for name in running]
                        front = rng.choice([0, 0, *(pids[name] for name in running)])
                        ages = [[pids[name], rng.choice([12.5, 400.25, 0.0])]
                                for name in running if rng.random() < 0.5]  # fmt: skip
                        steps.append([now, what, rows, front, ages])
                    elif what == "sample":
                        steps.append([now, what, rng.random() < 0.6])
                    else:
                        steps.append([now, what])
                services.append({"options": json.dumps(options), "home": str(tmp_path / "home"),
                                 "has_foreground": rng.random() < 0.6, "windows": False,
                                 "env": {}, "registry": [], "steps": steps})  # fmt: skip
            [ours] = run([{"op": "game", "locale": str(i18n.LOCALE_DIR), "language": language,
                           "services": services}])  # fmt: skip
            assert "services" in ours, ours

            seen = {key: 0 for key in ("logo", "entrance", "waited", "late", "told", "ended",
                                       "gone", "steam", "named", "other", "report")}  # fmt: skip
            for case, got in zip(services, ours["services"], strict=True):
                seen["report"] += len(got["reports"])
                waiting = False
                for (_now, what, *_more), found in zip(case["steps"], got["steps"], strict=True):
                    # (what it did: told a message, switched the mode (with an entrance or
                    # none), published a reading or the logo, wrote a note, named the program
                    # whose frames count; and its status after)
                    acts, status = found["acts"], json.loads(found["status"])
                    if any(act[0] == "note" and act[1].startswith("game: ") for act in acts):
                        seen["steam" if status["steam_id"] else "named"] += 1
                    if waiting and not status["waiting"]:  # (the mode began: the logo came, or not)
                        seen["late" if what == "advance" else "waited"] += 1
                    waiting = status["waiting"]
                    for act in acts:
                        seen["logo"] += act[0] == "logo" and act[1] is not None
                        seen["entrance"] += act[0] == "mode" and act[2] is not None
                        seen["told"] += act[0] == "tell"
                        seen["ended"] += act == ["note", "game over"]
                        seen["gone"] += act == ["note", "game gone before the mode began"]
                        seen["other"] += act[0] == "target" and len(acts) == 1
            assert all(count > 2 for count in seen.values()), seen
        finally:
            i18n.set_language("en")


@pytest.mark.kept
def test_the_autopilot_picks_the_theme_autopilot_py_picks() -> None:
    """services/autopilot.py: the rules read (what is wrong with one), the signals of each
    sample (music, the microphone, the load's mean over a window, a program, the time of
    day), the first rule that holds once it has held long enough and the theme before has
    stayed long enough, a theme that is not installed, a mode that rests it, and its
    status."""
    rng = random.Random(0xA07091)
    good = [
        {"name": "music", "theme": "a", "when": {"music": True}},
        {"theme": "b", "when": {"cpu_over": 50, "for_s": 5}},
        {"theme": "c", "when": {"apps": ["Code.exe", " Blender ", 5, ""]}, "hold_s": 0,
         "immediate": True},
        {"theme": "d", "when": {"time": "22:00-06:30"}, "hold_s": 1.5},
        {"theme": "d", "when": {"time": " 8:05 - 9:00 "}},
        {"theme": "e", "when": {"microphone": True, "gpu_over": 30.5}},
        {"theme": "missing", "when": {"music": False, "cpu_over": 10.0, "for_s": 99999}},
        {"name": 5, "theme": "a", "when": {"microphone": False, "music": False}},
    ]  # fmt: skip
    bad = [5, {"theme": ""}, {"name": "x"}, {"theme": "a", "when": 5}, {"theme": "a", "when": {}},
           {"theme": "a", "when": {"music": 1}}, {"theme": "a", "when": {"cpu_over": "x"}},
           {"theme": "a", "when": {"apps": []}}, {"theme": "a", "when": {"time": "25:00-01:00"}},
           {"theme": "a", "when": {"time": 5}}, {"theme": "a", "when": {"weird": 1}},
           {"theme": "a", "when": {"music": True, "for_s": 0}},
           {"theme": "a", "when": {"music": True}, "hold_s": -1},
           {"theme": "a", "when": {"music": True, "for_s": None}},
           {"theme": "a", "enabled": False, "when": {"music": True}},
           {"theme": "a", "when": {"for_s": 3}}]  # fmt: skip
    names = ["code.exe", "blender", "explorer.exe", "game.exe"]
    services = []
    for _ in range(300):
        options = {
            "rules": rng.sample(good, rng.randrange(5)) + rng.sample(bad, rng.randrange(3)),
            "hold_s": rng.choice([8.0, 0, 2]), "min_s": rng.choice([20.0, 0, 5]),
            "quiet_after_boot_s": rng.choice([240.0, 0]),
            "transition": rng.choice(["tube", "", "fade"]),
        }  # fmt: skip
        rng.shuffle(options["rules"])
        now, steps = rng.uniform(0, 500), []
        state = {"media.state": "Paused", "cpu.load": 20.0, "gpu.load": 10.0}
        machine = [False, None, [], rng.choice([None, 100.0, 500.0])]
        minutes = rng.randrange(1440)
        for _ in range(rng.randrange(1, 80)):
            now += rng.choice([0.2, 0.5, 1, 1, 3, 10])
            if rng.random() < 0.3:
                key = rng.choice(["media.state", "cpu.load", "gpu.load", "media.playing"])
                state[key] = rng.choice({"media.state": ["Playing", "playing", "Paused", None, 5],
                                         "media.playing": [1, 0, None]}.get(
                                             key, [5, 45.5, 60, 95.0, None, "x"]))  # fmt: skip
            if rng.random() < 0.3:
                machine[0] = rng.random() < 0.5
                machine[1] = rng.choice([None, *names])
                machine[2] = rng.sample(names, rng.randrange(4))
            if rng.random() < 0.2:
                minutes = rng.choice([rng.randrange(1440), 22 * 60, 6 * 60 + 30, 8 * 60 + 5])
            themes = rng.sample(["a", "b", "c", "d", "e"], rng.choice([5, 5, 5, 4, 3]))
            steps.append([now, json.dumps(state), minutes, rng.random() < 0.08, list(machine),
                          sorted(themes)])  # fmt: skip
        services.append({"options": json.dumps(options), "has_foreground": rng.random() < 0.5,
                         "steps": steps})  # fmt: skip
    [ours] = run([{"op": "autopilot", "services": services}])
    assert "services" in ours, ours

    seen = {"show": 0, "restore": 0, "missing": 0, "report": 0, "load": 0, "candidate": 0,
            "none": 0}  # fmt: skip
    for case, got in zip(services, ours["services"], strict=True):
        seen["report"] += len(got["reports"])
        seen["none"] += not got["rules"]
        assert len(got["steps"]) == len(case["steps"])
        if not got["rules"]:  # (no rule that holds: it does nothing)
            continue
        for found in got["steps"]:
            # (what it did: showed a theme, went back to the one before, wrote a note; and
            # its status after)
            acts, status = found["acts"], json.loads(found["status"])
            seen["load"] += status["signals"]["cpu"] is not None
            seen["candidate"] += status["candidate"] is not None
            seen["show"] += any(act[0] == "show" for act in acts)
            seen["restore"] += any(act[0] == "restore" for act in acts)
            seen["missing"] += any("is not installed" in str(act[1]) for act in acts)
    assert all(count > 20 for count in seen.values()), seen


def _change_files(change: list[Any]) -> None:
    """What lp-check's cases do to the files they look at (see change_files there)."""
    import os
    from pathlib import Path

    what, path = change[0], Path(change[1])
    if what == "write":
        path.write_bytes(bytes.fromhex(change[2]))
    elif what == "append":
        with open(path, "ab") as stream:
            stream.write(bytes.fromhex(change[2]))
    elif what == "size":
        with open(path, "ab"):
            pass
        os.truncate(path, change[2])
    elif what == "rename":
        path.replace(change[2])  # (as rename(2) does: over a file there, on Windows too)
    elif what == "remove":
        path.unlink()
    else:
        path.mkdir(parents=True, exist_ok=True)


@pytest.mark.kept
def test_downloads_are_followed_as_downloads_py_follows_them(tmp_path: Any, monkeypatch) -> None:
    """services/downloads.py: the Downloads and Pictures folders of what the system says, and
    a tracker's state after each look: Steam's log (its tail at the first look, a new log,
    lines not whole, bytes that are no UTF-8) and manifests (in a second library too), the
    browsers' part files (growing, renamed, paused, finished), what is too small, a download
    of unknown size, the one that matters most and how many more."""
    import shutil

    from libre_panel import i18n

    rng = random.Random(0xD0771)
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    bare = tmp_path / "bare"
    bare.mkdir()
    both = tmp_path / "both"
    (both / "Pictures").mkdir(parents=True)
    (both / "Documents").mkdir()
    folders = []
    for kind in ("downloads", "pictures"):
        for base in (home, bare, both):
            for found in (None, "", str(base), str(base) + "/", str(base / "x" / ".."), "rel/dl",
                          str(tmp_path / "moved"), str(base / "Pictures")):  # fmt: skip
                folders.append([kind, found, str(base)])

    def text(line: str) -> str:
        return line.encode().hex()

    states = ["Running Update,Downloading,", "Running Update,Verifying Staged,", "Preallocating,",
              "Update Required,Paused,", "None", "", "Update Required,", "Reconfiguring,",
              "Running,Committing,", "Suspended", "Running Update,Staging,",
              "Verifying Installed,", " Downloading "]  # fmt: skip
    appids = ["10", "20", "730"]
    names = ["Unconfirmed 123.crdownload", "movie.mkv.part", "Setup.EXE.PARTIAL", ".crdownload",
             "ünï.zip.crdownload", "small.part", "notes.txt"]  # fmt: skip

    def tracker_case(number: int) -> dict[str, Any]:
        root = tmp_path / f"t{number}"
        steam, lib2, folder = root / "steam", root / "lib2", root / "dl"
        user = root / "user"
        usual = user / ".local" / "share" / "Steam"
        where = rng.choice(["given", "given", "given", "usual", "wrong", "none"])
        steam = usual if where == "usual" else steam
        log = steam / "logs" / "content_log.txt"
        before: list[list[Any]] = [["mkdir", str(root)], ["mkdir", str(folder)],
                                   ["mkdir", str(user / "dl")]]  # fmt: skip
        if where != "none":
            before += [["mkdir", str(steam / "steamapps")], ["mkdir", str(steam / "logs")],
                       ["mkdir", str(lib2 / "steamapps")]]  # fmt: skip
            if rng.random() < 0.6:
                listed = _vdf({"libraryfolders": {"0": {"path": str(steam)},
                                                  "1": {"path": str(lib2)}}})  # fmt: skip
                before.append(["write", str(steam / "steamapps" / "libraryfolders.vdf"),
                               text(listed)])  # fmt: skip

        def line(appid: str) -> str:
            kind = rng.randrange(8)
            stamp = "[2026-01-02 03:04:05] "
            if kind == 0:
                total = rng.choice([0, 3_000_000, 80_000_000, 5_000_000_000])
                loaded = rng.choice([0, 0, total // 3])
                stage = rng.choice([0, total, 7])
                return (f"{stamp}AppID {appid} update started : download {loaded}/{total}, "
                        f"store 0/0, reuse 0/0, delta 0/0, stage 0/{stage}")  # fmt: skip
            if kind == 1:
                return f'{stamp}AppID {appid} finished update (0x6 "Fully Installed")'
            if kind == 2:
                return rng.choice([f"{stamp}Something else about {appid}", "", "AppID x"])
            state = "Running Update,Downloading," if kind > 4 else rng.choice(states)
            return f"{stamp}AppID {appid} App update changed : {state}"

        def lines(count: int) -> str:
            ends = rng.choice(["\n", "\n", "\r\n"])
            return "".join(line(rng.choice(appids)) + ends for _ in range(count))

        if where != "none" and rng.random() < 0.7:
            history = lines(rng.randrange(12))
            if rng.random() < 0.3:  # more than the tail that is read at the start
                history = "x" * rng.choice([262_100, 262_144, 300_000]) + history
            before.append(["write", str(log), text(history)])

        def manifest(appid: str) -> list[Any]:
            state = {"appid": appid, "name": rng.choice(["Half-Life™  2", "Portal", ""])}
            state["StateFlags"] = rng.choice(["4", "1026", "1282", "518", "x", "0", " 4 "])
            state["BytesToDownload"] = rng.choice(["0", "80000000", "5000000000", "", "1_0"])
            state["BytesDownloaded"] = str(rng.choice([0, 1_000_000, 40_000_000, 90_000_000]))
            for key in rng.sample(sorted(state), rng.randrange(2)):
                del state[key]
            table: dict[str, Any] = (
                {"AppState": state} if rng.random() < 0.95 else {"AppState": "x"}
            )
            library = lib2 if appid == "20" else steam
            return ["write", str(library / "steamapps" / f"appmanifest_{appid}.acf"),
                    text(_vdf(table))]  # fmt: skip

        sizes: dict[str, int] = {}
        now, steps = rng.uniform(0, 1000), []
        for _ in range(rng.randrange(1, 60)):
            now += rng.choice([1, 1, 1, 1, 1.5, 2, 7, 25, 70, 310])
            changes: list[list[Any]] = []
            if where != "none":
                roll = rng.random()
                if roll < 0.45:
                    changes.append(["append", str(log), text(lines(rng.randrange(1, 4)))])
                elif roll < 0.5:
                    changes.append(["append", str(log), text(line(rng.choice(appids)))])
                elif roll < 0.54:
                    changes.append(["append", str(log), b"AppID 10 \xff\xfe\xc3 \x0c\n".hex()])
                elif roll < 0.57:
                    changes.append(["write", str(log), text(lines(rng.randrange(3)))])
                if rng.random() < 0.4:
                    changes.append(manifest(rng.choice(appids)))
            roll = rng.random()
            if roll < 0.5:
                name = rng.choice(names)
                grown = sizes.get(name, 0) + rng.choice([0, 0, 300_000, 3_000_000, 40_000_000])
                sizes[name] = grown
                changes.append(["size", str(folder / name), grown])
            elif roll < 0.65 and sizes:
                name = rng.choice(sorted(sizes))
                size = sizes.pop(name)
                kind = rng.randrange(3)
                if kind == 0:  # done: the file it was becoming is there
                    whole = name[: name.rfind(".")] or name
                    if whole != name and whole not in sizes:
                        changes.append(["rename", str(folder / name), str(folder / whole)])
                    else:
                        sizes[name] = size
                elif kind == 1:  # the browser gives it its name
                    new = rng.choice(["big.iso.crdownload", "A.part"])
                    if new not in sizes:
                        changes.append(["rename", str(folder / name), str(folder / new)])
                        sizes[new] = size
                    else:
                        sizes[name] = size
                else:
                    changes.append(["remove", str(folder / name)])
            steps.append([now, rng.choice([None, 0, 300_000.0, 700_000, 2_000_000.0, 3e7]),
                          changes])  # fmt: skip
        given = {"given": str(steam), "usual": "", "wrong": str(root / "nothing"), "none": ""}
        return {
            "before": before, "steps": steps, "root": str(root),
            "steam": rng.random() < 0.85, "browser": rng.random() < 0.85,
            "min_mb": rng.choice([50.0, 50.0, 0, 1, 2.5, -3]),
            "steam_path": given[where],
            "folder": rng.choice([str(folder), str(folder), "", "~/dl", "~nobody_such_user/dl"]),
            "found": str(folder), "home": str(user), "env": {"HOME": str(user)},
        }  # fmt: skip

    for language, count in (("en", 120), ("ru", 40)):
        cases = [tracker_case(n) for n in range(count)]
        # (each case's folder is made by lp-check as its steps tell; the home folder of the
        # last one is the test's own while it runs, as it was when the answers were kept)
        for case in cases:
            shutil.rmtree(case["root"], ignore_errors=True)
        monkeypatch.setenv("HOME", cases[-1]["home"])
        [ours] = run([{"op": "downloads", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "folders": folders, "trackers": cases}])  # fmt: skip
        assert "trackers" in ours, ours
        for case in cases:
            shutil.rmtree(case["root"])
        assert len(ours["folders"]) == len(folders)
        seen = {"steam": 0, "browser": 0, "done": 0, "paused": 0, "installing": 0, "preparing": 0,
                "estimated": 0, "remaining": 0, "more": 0, "error": 0, "notes": 0, "none": 0,
                "unknown": 0, "App": 0}  # fmt: skip
        for case, got in zip(cases, ours["trackers"], strict=True):
            assert len(got["states"]) == len(case["steps"])
            seen["notes"] += len(got["notes"])
            for state in got["states"]:  # (what it shows after a look: nothing, why not, or it)
                if state is None:
                    seen["none"] += 1
                elif isinstance(state, dict):
                    seen["error"] += 1
                else:
                    state = json.loads(state)
                    seen[state["source"]] += 1
                    for phase in ("done", "paused", "installing", "preparing"):
                        seen[phase] += state["phase"] == phase
                    seen["estimated"] += state["estimated"]
                    seen["remaining"] += state["remaining_s"] is not None
                    seen["more"] += state["more"] > 0
                    seen["unknown"] += state["source"] == "steam" and state["total"] is None
                    seen["App"] += state["name"] != "Portal" and state["source"] == "steam"
        if os.name == "nt":  # (~nobody_such_user is a folder beside the user's there: no error)
            seen.pop("error")
        assert all(count >= 3 for count in seen.values()), seen


def _fancontrol_file(rng: random.Random) -> bytes:
    """A file as FanControl keeps one, or something else under that name."""
    sensors = ["/amdcpu/0/temperature/2", "/gpu-nvidia/0/temperature/0",
               "/lpc/nct6798d/temperature/1", "/intelcpu/0/temperature/0", "", 5, None]  # fmt: skip
    outputs = ["/lpc/nct6798d/control/0", "/lpc/nct6798d/control/1", "/gpu-nvidia/0/control/1",
               "/lpc/nct6798d/control/12\n", "weird", None, 5, ""]  # fmt: skip

    def curve() -> Any:
        if rng.random() < 0.05:
            return rng.choice([5, "x", None])
        points = [f"{rng.choice([20, 30, 45.5, 60, 60, 80])},{rng.choice([0, 20, 37.5, 55, 100])}"
                  for _ in range(rng.randrange(6))]  # fmt: skip
        points += rng.sample(["x", "40", 7, "50,abc", " 45 , 60 ", "1e1,2e1", ",", "inf,100"],
                             rng.randrange(3))  # fmt: skip
        rng.shuffle(points)
        found: dict[str, Any] = {
            "Points": rng.choice([points, points, None, "x"]),
            "SelectedTempSource": rng.choice(
                [{"Identifier": rng.choice(sensors)}, None, 5, {}]),
            "SelectedFanCurves": rng.choice(
                [None, [], [{"Name": "CPU"}, {"Name": ""}, 5, {"Name": 7}, {}]]),
            "SelectedMixFunction": rng.choice([0, 1, None]),
            "HysteresisConfig": rng.choice([{"Up": 2, "Down": rng.choice([3, 4.5])}, 5, None]),
        }  # fmt: skip
        if rng.random() < 0.9:
            found["Name"] = rng.choice(["CPU", "GPU", "Case", "Mix", "", " ", "Über"])
        for key in rng.sample(sorted(found), rng.randrange(3)):
            del found[key]
        return found

    def control() -> Any:
        if rng.random() < 0.05:
            return rng.choice([5, "x", None])
        found = {
            "NickName": rng.choice(["Front", "Rear", "", None, "  Pump ", 5]),
            "Identifier": rng.choice(outputs),
            "Enable": rng.choice([True, True, False, None, 1]),
            "IsHidden": rng.choice([True, False, False, None]),
            "SelectedFanCurve": rng.choice([{"Name": "CPU"}, {"Name": "GPU"}, None, {"Name": ""},
                                            {"Name": 5}, 5]),
            "MinimumPercent": rng.choice([20, 20.5, True, "x", None]),
            "SelectedOffset": rng.choice([0, 5]),
            "Window": rng.randrange(1000),
        }  # fmt: skip
        for key in rng.sample(sorted(found), rng.randrange(3)):
            del found[key]
        return found

    section: Any = {"FanCurves": [curve() for _ in range(rng.randrange(5))],
                    "Controls": [control() for _ in range(rng.randrange(5))],
                    "Other": 5}  # fmt: skip
    if rng.random() < 0.1:
        section = rng.choice([5, [], None, {"FanCurves": 5, "Controls": "x"}, {}])
    whole: Any = {"FanControl": section, "Version": 1}
    if rng.random() < 0.06:
        whole = rng.choice([[section], "x", {"Other": section}])
    raw = json.dumps(whole, ensure_ascii=rng.random() < 0.5).encode()
    kind = rng.random()
    if kind < 0.3:
        raw = b"\xef\xbb\xbf" + raw
    elif kind < 0.34:
        raw = raw[: len(raw) // 2]
    elif kind < 0.37:
        raw = b"\xff" + raw
    return raw


def _tables(path: str) -> dict[str, list[Any]]:
    """A database's tables, each with its rows in the order of their first column, and what
    made them (under "schema")."""
    import sqlite3

    connection = sqlite3.connect(path)
    try:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]  # fmt: skip
        tables = {name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
                  for name in names}  # fmt: skip
        tables["schema"] = connection.execute(
            "SELECT name, sql FROM sqlite_master ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return tables


def _digests(tables: dict[str, list[Any]]) -> dict[str, str]:
    """Each table's rows as a digest."""
    import hashlib

    return {name: hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()[:16]
            for name, rows in tables.items()}  # fmt: skip


@pytest.mark.kept
def test_the_memory_keeps_what_memory_py_keeps(tmp_path: Any, monkeypatch) -> None:
    """services/fancontrol.py (a file's state and mark, its curves with what they command,
    the fans, their names, what changed, the usual places, a watch over a file that
    changes), services/memory.py (the options, a sample of the readings, the minutes, the
    games, the cooling state as it moves, what is forgotten) into the database of
    memory_store.py, and services/round_report.py (a round against the earlier ones of its
    program, as a state and as readings; a round under way)."""
    import shutil
    from pathlib import Path

    from libre_panel import i18n

    rng = random.Random(0x3E3071)
    temps, duties = [10, 30.0, 45.5, 60, 61, 100], [0, 20, 37.5, 55, 99.5, 100]
    files = [
        {
            "file": _fancontrol_file(rng).hex(),
            "other": _fancontrol_file(rng).hex(),
            "temps": temps,
            "duties": duties,
        }
        for _ in range(400)
    ]
    places = [
        {"home": home, "env": dict(rng.sample(sorted(env.items()), rng.randrange(4)))}
        for home in ("/home/u", "/")
        for _ in range(6)
        for env in [
            {
                "ProgramFiles(x86)": "/pf86",
                "ProgramFiles": "/pf",
                "LOCALAPPDATA": rng.choice(["/local", ""]),
            }
        ]
    ]

    def watch_case(number: int) -> dict[str, Any]:
        root = tmp_path / f"w{number}"
        usual = root / "Documents" / "FanControl" / "Configurations"
        given = rng.choice(
            [
                "",
                str(root / "fc.json"),
                "~/fc.json",
                "~nobody_such_user/fc.json",
                str(root / "missing.json"),
            ]
        )
        file = usual / "userConfig.json" if not given else root / "fc.json"
        now, steps, there = rng.uniform(0, 100), [], False
        for step in range(rng.randrange(1, 14)):
            now += rng.choice([1, 10, 31, 31, 40])
            changes: list[list[Any]] = []
            roll = rng.random()
            if roll < 0.5:
                raw = _fancontrol_file(rng) + b" " * step  # (another size every time)
                changes.append(["write", str(file), raw.hex()])
                there = True
            elif roll < 0.6 and there:
                changes.append(["remove", str(file)])
                there = False
            steps.append([now, changes])
        return {
            "given": given,
            "steps": steps,
            "root": str(root),
            "home": str(root),
            "env": {"HOME": str(root), "USERPROFILE": str(root)},
            "windows": os.name == "nt",
            "before": [["mkdir", str(usual)]],
        }

    cpu, card, other = "/amdcpu/0/temperature/2", "/gpu-nvidia/0", "/gpu-amd/1"
    sensors = [
        ("cpu.load", "%", "CPU", ""),
        ("gpu.load", "%", "GPU", ""),
        ("cpu.temp", "°C", "CPU", f"lhm:{cpu}"),
        ("gpu.temp", "°C", "GPU", f"lhm:{card}/temperature/0"),
        ("temp.gpu1.junction", "°C", "Junction", f"lhm:{other}/temperature/3"),
        ("temp.gpu.hotspot", "°C", "GPU Hot Spot", f"lhm:{card}/temperature/1"),
        ("temp.gpu.hot_spot2", "°C", "Second", ""),
        ("cpu.power", "W", "", ""),
        ("gpu.power", "W", "", ""),
        ("cpu.freq", "MHz", "", ""),
        ("gpu.freq", "MHz", "", ""),
        ("mem.used", "GB", "", ""),
        ("net.down", "B/s", "", ""),
        ("net.up", "B/s", "", ""),
        ("audio.volume", "%", "", ""),
        ("power.watts", "W", "", ""),
        ("fan.cpu", "RPM", "CPU Fan", "lhm:/lpc/nct/fan/0"),
        ("fan.case", "RPM", "", "lhm:/lpc/nct/fan/1"),
        ("fan.x", "RPM", "CPU Fan", ""),
        ("fan.hidden", "RPM", "Hidden", "lhm:/lpc/nct/fan/5"),
        ("lhm:/lpc/nct/control/0", "%", "", ""),
        ("lhm:/lpc/nct/control/1", "%", "", ""),
        (f"lhm:{cpu}", "°C", "", ""),
        ("temp.board.system", "°C", "Board", ""),
        ("temp.nvme.0", "°C", "SSD", "lhm:/nvme/0/temperature/0"),
        ("temp.nvme.1", "°C", "", ""),
    ]

    def service_case(number: int) -> dict[str, Any]:
        options = dict(golden.given("memory_defaults"))  # (each as it is at first)
        options.update(
            keep_days=rng.choice([730.0, 0, 0.5]),
            cooling=rng.random() < 0.85,
            cooling_days=rng.choice([180.0, 0, 0.5]),
            music=rng.random() < 0.8,
            temperatures=rng.choice(
                [
                    [],
                    ["temp.board.system", "temp.nvme.*", "temp.none"],
                    ["temp.[bn]*", " ", 5, " temp.nvme.1 "],
                ]
            ),
            fps_levels=rng.choice(
                [[60, 90, 120, 144, 165], [60, 60, -1, "x", 30.5, True], [], [120.0]]
            ),
            report=rng.random() < 0.9,
            report_minutes=rng.choice([0, 0.05, 0.05, 3]),
            report_compare=rng.choice([1, 3, 10, 0]),
            report_seconds=rng.choice([30.0, 0]),
        )
        state = {key: rng.uniform(20, 80) for key, *_ in sensors}
        there = [row for row in sensors if rng.random() < 0.9]
        rng.shuffle(there)
        game: dict[str, Any] = {}
        now, steps, left = rng.uniform(1.7e9, 1.8e9), [], 0
        for _ in range(rng.randrange(1, 500)):
            now += (
                rng.choice([1, 1, 1, 1, 1, 1, 0.5, 2, 7, 65, 65]) if rng.random() < 0.995 else 90000
            )
            rows: list[list[Any]] = []
            for key, unit, label, origin in there:
                if rng.random() < 0.03:
                    continue
                if rng.random() < 0.5:
                    state[key] += rng.choice([-25, -3, -0.5, 0.5, 3, 25, 50])
                value: Any = state[key]
                odd = rng.random()
                if odd < 0.02:
                    value = rng.choice([None, "x", True, float("inf"), 0, int(value)])
                rows.append([key, value, unit, label, origin])
            if left == 0 and rng.random() < 0.04:
                left = rng.choice([2, 5, 30, 130, 260])
                game = {
                    "game.name": rng.choice(["Doom", "Doom", "Quake", ""]),
                    "game.app": rng.choice(["Doom.EXE ", "doom.exe", "quake", 5, None]),
                }
            if left:
                left -= 1
                rows += [
                    [key, value, "", "", ""] for key, value in game.items() if value is not None
                ]
                frames = {
                    "game.fps": rng.choice([30, 59.5, 60, 100.5, 144, 200, 0, None, -5]),
                    "game.low": rng.choice([20.5, 45, None, 0]),
                    "game.frametime": rng.choice([6.9, 16.6, None]),
                    "game.limiter": rng.choice(["cpu", "gpu", "gpu", "cap", "x", None, 5]),
                    "game.fps_avg": rng.choice([88.5, None]),
                    "game.fps_1low": 61.5,
                    "game.fps_01low": rng.choice([40.5, 0]),
                    "game.frametime_avg": 11.3,
                    "game.frametime_max": rng.choice([33.3, 80]),
                }
                if rng.random() < 0.9:
                    rows += [[k, v, "", "", ""] for k, v in frames.items() if rng.random() < 0.9]
            if rng.random() < 0.7:
                rows.append(
                    [
                        "gpu.mem.used",
                        rng.choice([2048, 6000.5, 8, None]),
                        rng.choice(["MB", "MB", "GB", "MiB", "", "B", "x"]),
                        "",
                        "",
                    ]
                )
            if rng.random() < 0.7:
                rows.append(
                    [
                        "weather.temperature",
                        rng.choice([-3.5, 21, 70, None]),
                        rng.choice(["°C", "°C", "°F"]),
                        "",
                        "",
                    ]
                )
            if rng.random() < 0.8:
                rows.append(
                    rng.choice(
                        [
                            ["media.state", rng.choice(["Playing", "Paused", 5]), "", "", ""],
                            ["media.playing", rng.choice([1, 0, None]), "", "", ""],
                        ]
                    )
                )
            if rng.random() < 0.7:
                rows.append(["cpu.name", rng.choice(["Ryzen 9", 5]), "", "", ""])
            steps.append([now, json.dumps(rows), rng.random() < 0.15, rng.random() < 0.03])
        return {
            "options": json.dumps(options),
            "steps": steps,
            "close": now + rng.choice([0, 1, 30]),
            "database": str(tmp_path / f"rust{number}.db"),
            "theirs": str(tmp_path / f"py{number}.db"),
            "names": rng.choice([{}, {"/lpc/nct/fan/0": "Tower", "/lpc/nct/fan/1": "Tower"}]),
            "hidden": rng.choice([[], ["/lpc/nct/fan/5"]]),
            "sources": rng.choice([{}, {"CPU": cpu, "Case": "/lpc/nct/temperature/9"}]),
        }

    seen = {
        "file": 0,
        "unread": 0,
        "curve": 0,
        "fan": 0,
        "changes": 0,
        "watch": 0,
        "watch-error": 0,
        "report": 0,
        "compared": 0,
        "levels": 0,
        "round": 0,
        "sensors": 0,
        "minutes": 0,
        "sessions": 0,
        "cooling": 0,
        "course": 0,
        "notes": 0,
        "fans": 0,
        "temps": 0,
        "sources": 0,
        "outside": 0,
        "music": 0,
        "hotspot": 0,
    }
    for language, count in (("en", 30), ("ru", 10)):
        watches = [watch_case(n) for n in range(40)]
        services = [service_case(n) for n in range(count)]
        # (each watch's folder is made by lp-check as its steps tell; the home folder of the
        # last one is the test's own while it runs, as it was when the answers were kept)
        for case in watches:
            shutil.rmtree(case["root"], ignore_errors=True)
        monkeypatch.setenv("HOME", watches[-1]["home"])
        monkeypatch.setenv("USERPROFILE", watches[-1]["home"])
        [ours] = run(
            [
                {
                    "op": "memory",
                    "locale": str(i18n.LOCALE_DIR),
                    "language": language,
                    "files": files,
                    "places": places,
                    "watches": watches,
                    "services": services,
                }
            ]
        )
        assert "services" in ours, ours
        for case in watches:
            shutil.rmtree(case["root"])
        assert len(ours["files"]) == len(files) and len(ours["places"]) == len(places)
        for got in ours["files"]:  # (what a file of FanControl's holds; None: not read)
            seen["unread"] += got is None
            if got is not None:
                seen["file"] += 1
                seen["curve"] += got["curves"] != "[]"
                seen["fan"] += got["fans"] != "[]"
                seen["changes"] += got["changes"] not in (None, "[]")
        for got in ours["watches"]:
            seen["watch"] += sum(isinstance(found, str) for found in got["found"])
            seen["watch-error"] += sum(isinstance(found, dict) for found in got["found"])
        for case, got in zip(services, ours["services"], strict=True):
            # (the database it wrote, table by table as the Python app's of the same case was)
            tables = _tables(case["database"])
            assert _digests(tables) == golden.told("tables"), case["options"]
            for end in ("", "-wal", "-shm"):
                Path(case["database"] + end).unlink(missing_ok=True)
            seen["notes"] += len(got["notes"])
            seen["report"] += len(got["reports"])
            for report, _readings, _stamp in got["reports"]:
                comparison = json.loads(report)["comparison"]
                seen["compared"] += comparison["count"] > 0
                seen["levels"] += comparison["levels"] is not None
            seen["round"] += sum(said is not None for said in got["rounds"])
            seen["sensors"] += sum('"panel": "gpu_hotspot"' in text for text in got["sensors"])
            seen["minutes"] += len(tables["minutes"])
            seen["sessions"] += len(tables["sessions"])
            seen["cooling"] += len(tables["cooling"])
            seen["course"] += sum(row[-1] is not None for row in tables["sessions"])
            seen["fans"] += sum(row[-2] is not None for row in tables["cooling"])
            seen["temps"] += sum(row[-3] is not None for row in tables["cooling"])
            seen["sources"] += sum(row[-1] is not None for row in tables["cooling"])
            seen["outside"] += sum(row[-2] is not None for row in tables["minutes"])
            seen["music"] += sum(bool(row[-3]) for row in tables["minutes"])
            seen["hotspot"] += sum(row[8] is not None for row in tables["minutes"])
    assert all(count >= 5 for count in seen.values()), seen


def _cooling_case(rng: random.Random, path: Any, number: int) -> dict[str, Any]:
    """A database as the memory writes one: some days at the desk and in games, with fans
    that follow their curves or do not, FanControl's states and the names of the sensors;
    and FanControl's file of the same curves."""
    import sqlite3

    tables = golden.given("memory_schema")  # (the database's, as the memory makes them)

    def at(points: list[tuple[float, float]], temp: float) -> float:
        if temp <= points[0][0]:
            return points[0][1]
        for (t0, d0), (t1, d1) in zip(points, points[1:], strict=False):
            if temp <= t1:
                return d0 + (d1 - d0) * (temp - t0) / (t1 - t0)
        return points[-1][1]

    cpu_points = [(30.0, rng.choice([20.0, 30.0])), (60.0, 50.0), (80.0, 100.0)]
    gpu_points = [(40.0, 25.0), (70.0, 60.0), (85.0, 100.0)]
    board_points = rng.choice([[(30.0, 30.0), (50.0, 60.0), (70.0, 90.0), (80.0, 100.0)], []])
    wrong = rng.choice([0.0, 0.0, 15.0, -12.0])  # the CPU's curve reads another sensor
    highest = rng.random() < 0.5  # the mix takes the higher of its parts
    jumpy = rng.random() < 0.3
    hot = rng.random() < 0.4
    loud = rng.choice([0.0, 0.0, 12.0])
    drift = rng.choice([0.0, 0.0, 0.5])
    kept = rng.random() < 0.5  # what each curve reads is recorded
    days = rng.choice([1, 2, 3, 16, 20])
    hours = 2.0 if days > 5 else rng.choice([0.3, 2.0, 8.0])
    began = 1_750_000_000 + number * 1000
    rows, ts = [], began
    for day in range(days):
        ts = began + day * 86400
        outside = rng.choice([None, 12.0 + 6 * rng.random()])
        for kind, share in (("desktop", 0.5), ("game", 0.3), ("desktop", 0.2)):
            if kind == "game" and rng.random() < 0.2:
                continue
            end = ts + hours * 3600 * share
            game = rng.choice(["Doom", "Doom", "Quake", None]) if kind == "game" else None
            flip = 1
            while ts < end:
                if kind == "game":
                    load, step = rng.uniform(50, 100), rng.choice([5, 8, 20, 60, 60])
                    cpu = 62 + load * 0.15 + rng.uniform(-2, 2)
                    gpu = (82 if hot else 70) + rng.uniform(-3, 6)
                else:
                    load, step = rng.uniform(3, 30), rng.choice([12, 20, 60, 60, 60])
                    cpu = 36 + load * 0.3 + drift * day + rng.uniform(-1.5, 1.5)
                    gpu = 41 + rng.uniform(-2, 2)
                flip = -flip
                wobble = 6.5 * flip if jumpy else rng.uniform(-1, 1)
                seen = cpu + wrong
                cpu_duty = min(100.0, max(0.0, at(cpu_points, seen) + loud + wobble))
                gpu_duty = min(100.0, at(gpu_points, gpu) + rng.uniform(-1, 1))
                mixed = max(cpu_duty, gpu_duty) if highest else min(cpu_duty, gpu_duty) - 3
                board = 38 + rng.uniform(-1, 1)
                fans = {"CPU Fan": [round(cpu_duty * 15 + rng.uniform(-20, 20)), cpu_duty],
                        "GPU fans": [None if rng.random() < 0.05 else gpu_duty * 22.0, gpu_duty],
                        "Case": [mixed * 11.0, mixed]}  # fmt: skip
                if board_points:
                    duty = at(board_points, board + 20)
                    fans["Rear Fan"] = [duty * 9.0, None if rng.random() < 0.02 else duty]
                sources = {"CPU": round(seen, 1), "GPU": round(gpu, 1)} if kept else {}
                rows.append((
                    ts, "game" if kind == "game" else "normal", game, cpu,
                    rng.uniform(40, 120), load, gpu, gpu + 12 if rng.random() < 0.9 else None,
                    rng.uniform(30, 300), load, outside,
                    json.dumps({"temp.board.system": board}) if rng.random() < 0.9 else None,
                    json.dumps(fans), json.dumps(sources) if sources else None,
                ))  # fmt: skip
                ts += step
            ts += rng.choice([0, 0, 200])
    mix = {"Name": "Mix", "SelectedFanCurves": [{"Name": "CPU"}, {"Name": "GPU"}]}

    def curve(name: str, points: list[tuple[float, float]], source: str) -> dict[str, Any]:
        return {"Name": name, "Points": [f"{t:g},{d:g}" for t, d in points],
                "SelectedTempSource": {"Identifier": source},
                "HysteresisConfig": rng.choice([{}, {"HysteresisValueUp": 2,
                                                     "HysteresisValueDown": 3.5,
                                                     "ResponseTimeDown": 4}])}  # fmt: skip

    def control(name: str, on: str, number: int) -> dict[str, Any]:
        return {
            "NickName": name,
            "Identifier": f"/lpc/nct/control/{number}",
            "Enable": True,
            "SelectedFanCurve": {"Name": on},
            "MinimumPercent": rng.choice([20, None, 30.5]),
        }

    section = {
        "FanCurves": [curve("CPU", cpu_points, "/amdcpu/0/temperature/2"),
                      curve("GPU", gpu_points, "/gpu-nvidia/0/temperature/0"), mix,
                      curve("Board", board_points, "/lpc/nct/temperature/1")],
        "Controls": [control("CPU Fan", "CPU", 0), control("GPU fans", "GPU", 1),
                     control("Case", "Mix", 2), control("Rear_Fan", "Board", 3)],
    }  # fmt: skip
    connection = sqlite3.connect(path)
    try:
        connection.executescript(tables["schema"])
        marks = ", ".join("?" * tables["cooling_columns"])
        connection.executemany(f"INSERT INTO cooling VALUES ({marks})", rows)
        for n in range(rng.choice([0, 1, 2, 2, 3])):
            state = {"curves": [dict(c, Points=[*c.get("Points", []), f"{90 + n},100"])
                                for c in section["FanCurves"]],
                     "controls": section["Controls"]}  # fmt: skip
            when = began + int((ts - began) * [0.0, 0.3, 0.6][n])
            connection.execute("INSERT INTO fancontrol VALUES (?, ?, ?)",
                               (when, f"mark{n}", json.dumps(state)))  # fmt: skip
        if rng.random() < 0.8:
            named = {
                "cpu": rng.choice(["Ryzen 7", None]),
                "gpu": "RTX 4070",
                "sensors": [
                    {
                        "key": "temp.cpu.core",
                        "id": "/amdcpu/0/temperature/2",
                        "label": "Core (Tctl/Tdie)",
                        "panel": "cpu_temp",
                    },
                    {
                        "key": "temp.gpu.core",
                        "id": rng.choice([None, "/gpu-nvidia/0/temperature/0"]),
                        "label": rng.choice(["GPU Core", ""]),
                        "panel": "gpu_temp",
                    },
                    {"key": "temp.board.system", "id": None, "label": "System", "panel": None},
                ],
            }
            connection.execute("INSERT INTO meta VALUES ('sensors', ?)", (json.dumps(named),))
        connection.commit()
    finally:
        connection.close()
    return {
        "database": str(path), "file": json.dumps({"FanControl": section}).encode().hex(),
        "limits": json.dumps(rng.choice([[], [{"key": "gpu.temp", "max": 80, "name": "Card"},
                                              {"key": "temp.board.system", "max": 30.5}, 5,
                                              {"key": "", "max": 1}, {"key": "x", "max": True}]])),
        "days": rng.choice([None, 1.0, 0.5, 30]),
        "mode": rng.choice([None, None, "game", "desktop"]),
        "points": rng.choice([480, 480, 5, 40]), "now": float(ts + rng.choice([0, 600, 40000])),
    }  # fmt: skip


@pytest.mark.kept
def test_the_cooling_is_judged_as_cooling_py_judges_it(tmp_path: Any) -> None:
    """services/cooling.py with cooling_data.py, cooling_advice.py and cooling_text.py: the
    rows of a database with the time each holds, the recommendations since FanControl was
    last changed (a curve on the wrong sensor, a mix that does not take the higher part,
    what is too warm, oscillation, a fan that could be quieter, the trend), everything a page
    draws (the course, the clouds, the findings, the table) and the report in words."""
    from pathlib import Path

    from libre_panel import i18n

    rng = random.Random(0xC0071)
    seen = {key: 0 for key in ("source", "mix", "warm", "oscillation", "quieter", "trend", "none",
                               "changed", "waiting", "effect", "warn", "ok", "proposal", "report",
                               "notes", "measured", "after")}  # fmt: skip
    for language, count in (("en", 36), ("ru", 12)):
        cases = [_cooling_case(rng, tmp_path / f"{language}{n}.db", n) for n in range(count)]
        [ours] = run([{"op": "cooling", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "cases": cases}])  # fmt: skip
        assert "cases" in ours, ours
        for case, got in zip(cases, ours["cases"], strict=True):
            recommended = json.loads(got["recommended"])
            if got["rows"]:  # (what a page draws of them, and the report in words)
                drawn = json.loads(got["analysis"])
                for finding in drawn["findings"]:
                    seen[finding["level"]] = seen.get(finding["level"], 0) + 1
                seen["measured"] += any(plot["measured"] for plot in drawn["plots"])
                seen["proposal"] += any(plot["proposal"] for plot in drawn["plots"])
                seen["report"] += len(got["report"])
            seen["notes"] += len(got["notes"])
            seen["none"] += not recommended["list"]
            seen["changed"] += recommended["changed"]
            seen["waiting"] += "waiting" in recommended
            seen["effect"] += bool(recommended["effect"])
            for entry in recommended["list"]:
                seen[entry["id"].split(":")[0]] += 1
                seen["after"] += "after_number" in entry
            Path(case["database"]).unlink()
    assert all(count >= 2 for count in seen.values()), seen


@pytest.mark.kept
def test_devices_are_told_as_devices_py_tells_them() -> None:
    """services/devices.py and devices_windows.py: the ids of `usb_ignore`, sizes in words,
    what a drive's descriptors say, the USB devices that are told about, and a watcher's
    events over what comes and goes (drives, controllers and their batteries, USB devices
    that settle, a drive nearly full, an SSD that runs hot; four changes at once taken in
    silently), each with its message."""
    import struct

    from libre_panel import i18n

    rng = random.Random(0xDE71CE5)
    ignored = [rng.sample(["1a86:7523", " 05AC:12a8 ", "1a867523", "zzzz:0000", 5, "", None,
                           "12345:6789", "ABCD:EF01\n", "abcd:ef0g"], rng.randrange(6))
               for _ in range(60)]  # fmt: skip
    sizes = [0, 5e5, 999_999_999, 1e9, 9.99e9, 1e10, 2.98e10, 512 * 10**9, 1e12, 2.5e13, 123456789]
    sizes += [rng.uniform(0, 3e12) for _ in range(60)]

    def descriptor() -> bytes:
        kind = rng.randrange(3)
        if kind == 0:  # STORAGE_DEVICE_DESCRIPTOR
            maker, product = rng.choice([b"Samsung ", b"", b" WD\xff"]), rng.choice([b"T7 ", b""])
            head = struct.pack("<IIBBBBIIIII", 40, 40 + len(maker) + len(product) + 2, 0, 0, 1,
                               0, rng.choice([0, 36, 400]), 36 + len(maker) + 1,
                               rng.choice([0, 99]), rng.choice([7, 12, 13, 17, 11]), 0)  # fmt: skip
            return head + maker + b"\0" + product + rng.choice([b"\0", b""])
        if kind == 1:  # STORAGE_TEMPERATURE_DATA_DESCRIPTOR
            count = rng.randrange(4)
            head = struct.pack("<IIhhHH", 40, 24 + 16 * count, rng.choice([0, 85, 200, -5]),
                               rng.choice([0, 70, 150]), count, 0) + bytes(8)  # fmt: skip
            infos = b"".join(struct.pack("<HhhhII", n, rng.choice([-50, 35, 62, 149, 150]),
                                         0, 0, 0, 0) for n in range(count))  # fmt: skip
            return (head + infos)[: rng.choice([1000, 1000, 39, 44])]
        return bytes(rng.randrange(256) for _ in range(rng.randrange(80)))

    raw = [descriptor() for _ in range(300)]
    idents = ["USB\\VID_1A86&PID_7523\\5&1c2", "usb\\vid_05ac&pid_12A8\\0001",
              "USB\\VID_1A86&PID_7523&MI_00\\6&2", "USB\\VID_1A86&PID_752\\x", "USB\\ROOT_HUB30\\4",
              "USB\\VID_1A86&PID_7523\\", "USB\\VID_1A86&PID_7523\\a\\b", "", "HID\\VID_1&PID_2\\1",
              "USB\\VID_GGGG&PID_0000\\1", "USB\\VID_1A86&PID_7523\\x\n"]  # fmt: skip
    usb = []
    for _ in range(200):
        entry = {
            "vid": rng.choice(["1A86", "05AC"]),
            "pid": rng.choice(["7523", "12A8"]),
            "name": rng.choice(["USB Hub", "Generic hub", "iPhone", "", None, "CH340"]),
            "klass": rng.choice(["USB", "usb", "Ports", "", None]),
            "service": rng.choice(["usbhub3", "USBSTOR", "usbser", "xboxgip", "", None]),
        }
        for key in rng.sample(["name", "klass", "service"], rng.randrange(2)):
            del entry[key]
        usb.append([json.dumps(entry), rng.sample(["1A86:7523", "05AC:12A8", "0000:0000"],
                                                  rng.randrange(3))])  # fmt: skip

    def watcher_case() -> dict[str, Any]:
        options = {"drives": rng.random() < 0.9, "usb": rng.random() < 0.9,
                   "controllers": rng.random() < 0.9, "storage": rng.random() < 0.9,
                   "min_free_gb": rng.choice([25.0, 0, 100]),
                   "min_free_percent": rng.choice([5.0, 0, 50]),
                   "ssd_warn_c": rng.choice([0.0, 0.0, 60, -1])}  # fmt: skip
        drives: dict[str, Any] = {}
        pads: dict[str, Any] = {}
        sticks: dict[str, Any] = {}
        disks: dict[str, Any] = {"C": {"name": "SSD 980", "free": 300e9, "total": 1000 * 10**9,
                                       "temp": 40.0, "warn": 70.0, "crit": 85.0}}  # fmt: skip
        now, steps, told = rng.uniform(0, 100), [], None
        for _ in range(rng.randrange(1, 90)):
            now += rng.choice([0.1, 0.25, 0.25, 0.5, 1, 1, 2.5, 5, 70])
            roll = rng.random()
            if roll < 0.12:
                letter = rng.choice("EFGHIJ")
                if letter in drives:
                    del drives[letter]
                else:
                    total = rng.choice([0, 32 * 10**9, 2 * 10**12])
                    drives[letter] = {"name": rng.choice(["SanDisk Ultra", ""]),
                                      "label": rng.choice(["STICK", ""]),
                                      "free": int(total * rng.random()), "total": total,
                                      "bus": rng.choice(["USB", "SD"])}  # fmt: skip
            elif roll < 0.15:
                drives = {letter: {"name": "", "label": "", "free": 1, "total": 2, "bus": "USB"}
                          for letter in rng.sample("KLMNOP", 5)}  # fmt: skip
            elif roll < 0.3:
                slot = str(rng.randrange(4))
                if slot in pads and rng.random() < 0.4:
                    del pads[slot]
                else:
                    pads[slot] = {"wireless": rng.random() < 0.5,
                                  "battery": rng.choice([1, 2, 3, 3, 0, 255, None]),
                                  "level": rng.choice([0, 1, 2, 3, None])}  # fmt: skip
            elif roll < 0.45:
                key = (
                    f"USB\\VID_{rng.choice(['05AC', '1A86', '046D'])}&PID_0001\\{rng.randrange(3)}"
                )
                if key in sticks:
                    del sticks[key]
                else:
                    sticks[key] = {"vid": key[8:12], "pid": "0001",
                                   "name": rng.choice(["iPhone", "Receiver", ""]),
                                   "klass": "USB", "service": "x"}  # fmt: skip
            elif roll < 0.47:
                sticks = {
                    f"USB\\VID_0000&PID_000{n}\\1": {"vid": "0000", "pid": f"000{n}", "name": "x"}
                    for n in range(5)
                }
            elif roll < 0.65:
                letter = rng.choice("CD")
                if letter in disks and rng.random() < 0.15:
                    del disks[letter]
                else:
                    total = rng.choice([1000 * 10**9, 250 * 10**9, 0])
                    disks[letter] = {"name": rng.choice(["SSD 980", "", None]),
                                     "free": rng.choice([1e9, 9e9, 30e9, 300e9]), "total": total,
                                     "temp": rng.choice([None, 40.0, 64, 71.5, 80, 86, 95]),
                                     "warn": rng.choice([None, 70.0, 0]),
                                     "crit": rng.choice([None, 85.0, 0])}  # fmt: skip
            if rng.random() < 0.2:
                told = now - rng.choice([0, 0.5, 1.5])
            steps.append([now, told, rng.random() < 0.15, json.dumps(drives), json.dumps(pads),
                          json.dumps(sticks), json.dumps(disks)])  # fmt: skip
        return {"options": json.dumps(options), "events": rng.random() < 0.5, "steps": steps}

    seen: dict[str, int] = dict.fromkeys(
        ("drive-in", "drive-out", "controller-in", "controller-out", "controller-low", "usb-in",
         "usb-out", "space", "heat", "busy", "error", "progress", "phone"), 0)  # fmt: skip
    for language, count in (("en", 300), ("ru", 100)):
        watchers = [watcher_case() for _ in range(count)]
        [ours] = run([{"op": "devices", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "ignored": [json.dumps(given) for given in ignored], "sizes": sizes,
                       "raw": [blob.hex() for blob in raw], "idents": idents, "usb": usb,
                       "watchers": watchers}])  # fmt: skip
        assert "watchers" in ours, ours
        for key, given in (("ignored", ignored), ("sizes", sizes), ("raw", raw),
                           ("idents", idents), ("usb", usb)):  # fmt: skip
            assert len(ours[key]) == len(given), key
        for case, got in zip(watchers, ours["watchers"], strict=True):
            assert len(got) == len(case["steps"])
            for told, pause in got:  # (a step's events, each with its message; and its pause)
                for what, _key, _data, said in told:
                    seen[what] += 1
                    seen["error"] += said["level"] == "error"
                    seen["progress"] += "progress" in json.loads(said["payload"])
                    seen["phone"] += said["icon"] == "phone"
                seen["busy"] += pause < 1
    assert all(count >= 5 for count in seen.values()), seen


@pytest.mark.kept
def test_the_waveshare_display_is_fed_as_waveshare_py_feeds_it() -> None:
    """devices/waveshare.py and services/waveshare.py: frames, Qt's maps written and read back,
    frames found in what a port gives piece by piece, the ports the display may be on, the
    values of a snapshot under the display's keys, what stands in the way, and whole sessions
    with a display the test makes up (the handshake, version, brightness, the picture turned,
    the themes on the card, a theme shown, themes in turn, values until it falls silent): the
    bytes written and what the service says of itself."""
    import struct
    import zlib

    from libre_panel import i18n

    def frame(command: int, payload: bytes = b"", kind: int = 0) -> bytes:
        """A whole frame of `command` with `payload` (to the display: 0; its answer: 2)."""
        head = struct.pack("<4I", kind, command, len(payload), zlib.crc32(payload))
        return b"AA551234 FIXEDCMDHEAD " + head + payload

    def qmap(values: dict[str, str]) -> bytes:
        """`values` as Qt 5's QDataStream writes a QMap<QString, QString>: from the last key
        to the first, in the order of their UTF-16 code units."""
        out = [struct.pack(">I", len(values))]
        for key in sorted(values, key=lambda k: k.encode("utf-16-be"), reverse=True):
            for text in (key, values[key]):
                data = str(text).encode("utf-16-be")
                out.append(struct.pack(">I", len(data)) + data)
        return b"".join(out)

    rng = random.Random(0x5AFE5A)
    words = ["Cpu_Temp", "Cpu温度", "canvas_flip", "a", "", "Z", "ａ", "😀x", "é", "week", "0"]
    texts = ["", "75%", "31℃", "Mon", "x" * 40, "😀", "1", "ß"]

    def a_map() -> list[list[str]]:
        keys = rng.sample(words, rng.randrange(len(words)))
        return [[key, rng.choice(texts)] for key in keys]

    def a_payload() -> bytes:
        roll = rng.random()
        whole = qmap(dict(a_map()))
        if roll < 0.4:
            return whole
        if roll < 0.5:
            return whole[: rng.randrange(len(whole) + 1)] + bytes(rng.randrange(3))
        if roll < 0.6:  # (null strings)
            return b"\0\0\0\x01\xff\xff\xff\xff" + rng.choice(
                [b"\xff\xff\xff\xff", b"\0\0\0\x02\0a"]
            )
        if roll < 0.7:
            return rng.choice([b"128", b"", b"\xff\xfe", b"\0\0\0\x01\0\0\0\x02\xd8\0\0\0\0\0"])
        return bytes(rng.randrange(256) for _ in range(rng.randrange(24)))

    frames = [[rng.choice([0, 1, 2, 3, 4, 8, 9, 10, 77]), a_payload().hex(), rng.choice([0, 2])]
              for _ in range(80)]  # fmt: skip
    maps = [a_map() for _ in range(120)]
    payloads = [a_payload().hex() for _ in range(300)]

    def a_feed() -> list[str]:
        stream = bytearray()
        for _ in range(rng.randrange(1, 6)):
            stream += bytes(rng.randrange(256) for _ in range(rng.choice([0, 0, 3, 30])))
            whole = bytearray(frame(rng.randrange(11), a_payload(), rng.choice([0, 2])))
            roll = rng.random()
            if roll < 0.15:
                whole[-1] ^= 1  # (the CRC does not fit, or the head is broken)
            elif roll < 0.25:
                whole[30:34] = b"\xff\xff\xff\x7f"
            elif roll < 0.3:
                whole = whole[: rng.randrange(len(whole))]
            stream += whole
        pieces = []
        while stream:
            count = rng.choice([1, 5, 21, 22, 40, 4096])
            pieces.append(bytes(stream[:count]).hex())
            del stream[:count]
        return pieces

    feeds = [a_feed() for _ in range(200)]
    names = ["/data/theme/480x480/Space Suit.Theme", "Space.Theme", "/a/b", "", "x.Theme/",
             ".Theme", "/data/theme/a.theme", "ä/ö.Theme"]  # fmt: skip
    products = (None, "Virtual Serial Port", " Virtual Serial Port\n", "Hub", "")
    ids = [[vid, pid, product] for vid in (0x1D6B, 0x1234, 0x303A, None)
           for pid in (0x0104, 0x5678, None) for product in products]  # fmt: skip
    reasons = ["COM5: Access is denied.", "/dev/ttyACM0 is busy: wsmonitor has it open",
               "[Errno 11] Resource temporarily unavailable", "[Errno 13] Permission denied: 'x'",
               "COM5: the display did not answer", "no Waveshare USB monitor is connected", "",
               "BUSY", "İ did not answer"]  # fmt: skip

    def readings() -> dict[str, Any]:
        number = [0, 1, 99, 42.5, 2.5, -0.4, 0.05, 99.95, 1e17, 123456.789, True, False, None,
                  "text", "", 102400, 102399.9, 1023.9]  # fmt: skip
        keys = golden.given("waveshare_keys") + [
            "fan.cpu", "fan.board.CPU_FAN", "fan.cpu.percent", "fan.lpc.pump", "fan.1",
            "fan.pump.percent", "fan.Pumpe", "disk.load", "disk.1.load", "disk.3.load",
            "disk.8.load", "other"]  # fmt: skip
        found = {key: rng.choice(number) for key in rng.sample(keys, rng.randrange(len(keys)))}
        for key in ("cpu.name", "gpu.name", "mem.name", "weather.description"):
            if key in found and rng.random() < 0.7:
                found[key] = rng.choice(["Ryzen 7", "", "RTX 4070 ℃", 5])
        return found

    def a_moment() -> list[int]:
        return [rng.randrange(1999, 2040), rng.randrange(1, 13), rng.randrange(1, 29),
                rng.randrange(24), rng.randrange(60), rng.randrange(60)]  # fmt: skip

    valued = [[rng.choice([None, readings(), readings()]), a_moment(), rng.random() < 0.5]
              for _ in range(150)]  # fmt: skip
    on_card = ["/data/theme/480x480/Space Suit.Theme", "/data/theme/480x480/alpha.Theme",
               "/data/theme/480x480/Beta.Theme"]  # fmt: skip

    def a_session() -> dict[str, Any]:
        about = rng.choice([
            {"screen_w": "480", "screen_h": "480", "screen_inch": "2.8"}, {}, {"screen_w": "480"},
        ])  # fmt: skip
        listed = {path: "sum" for path in rng.sample(on_card, rng.randrange(4))}
        listed.update(rng.choice([{}, {"version": "1"}, {"/data/theme/x.txt": "1"}]))
        answers: dict[str, list[str]] = {
            "0": [rng.choice([qmap(about), b"", b"hello"]).hex()] * rng.choice([1, 1, 1, 0]),
            "8": [rng.choice([qmap({"versionV2": "2.1"}), b"V1.0", b"",
                              qmap({"other": "1"})]).hex()] * rng.choice([1, 1, 0]),
            "4": [b"128".hex()] * rng.choice([1, 1, 1, 0]),
            "9": [b"".hex()] * rng.choice([0, 1, 2, 5, 5]),
            "3": [rng.choice([qmap(listed), b"", b"no"]).hex()] * rng.choice([1, 1, 0]),
            "2": [b"".hex()] * rng.choice([0, 1, 3, 3]),
            "1": [b"".hex()] * rng.randrange(5),
        }  # fmt: skip
        options = {
            "port": rng.choice(["", " COM7 "]), "brightness": rng.choice([0, 0, 1, 50, 100, 250]),
            "flip": rng.choice(["", "normal", "upside-down", " upside-down ", "sideways"]),
            "units": rng.random() < 0.5, "every_s": rng.choice([1.0, 0.1]),
            "theme": rng.choice(["", on_card[0], " " + on_card[1], "/data/theme/gone.Theme"]),
            "rotate": rng.choice([[], on_card[:2], on_card + [" ", "/data/theme/gone.Theme"],
                                  on_card[:1]]),
            "rotate_min": rng.choice([0, 0.0, 5, -1]),
        }  # fmt: skip
        return {"options": json.dumps(options), "answers": answers,
                "noise": rng.choice(["", "", "00ff41413535"]), "piece": rng.choice([4096, 7, 1]),
                "rotate_s": rng.choice([None, None, -1.0]), "readings": json.dumps(readings()),
                "now": a_moment()}  # fmt: skip

    connects = [
        {"port": "COM7", "answers": {"0": [qmap({"screen_w": "480"}).hex()]}, "noise": "",
         "piece": 4096, "fails": None},
        {"port": "COM7", "answers": {"0": [""]}, "noise": "0102", "piece": 3, "fails": None},
        {"port": "/dev/ttyACM9", "answers": {}, "noise": "", "piece": 1, "fails": "is busy"},
    ]  # fmt: skip

    seen = dict.fromkeys(("connected", "theme", "turn", "themes", "silent"), 0)
    for language in ("en", "de"):
        sessions = [a_session() for _ in range(150)]
        [ours] = run([{"op": "waveshare", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "frames": frames, "maps": maps, "payloads": payloads, "feeds": feeds,
                       "names": names, "ids": ids, "reasons": reasons,
                       "values": [[json.dumps(found), moment, units]
                                  for found, moment, units in valued],
                       "sessions": sessions, "connects": connects}])  # fmt: skip
        assert "sessions" in ours, ours
        assert ours["frames"] == [frame(c, bytes.fromhex(p), k).hex() for c, p, k in frames]
        assert ours["maps"] == [qmap(dict(pairs)).hex() for pairs in maps]
        for key, given in (("payloads", payloads), ("feeds", feeds), ("names", names),
                           ("ids", ids), ("kinds", reasons), ("values", valued),
                           ("connects", connects)):  # fmt: skip
            assert len(ours[key]) == len(given), key
        # (a session: what the display says of itself or nothing, how it ended, the service's
        # state, the bytes written, its notes)
        for about, _ended, state, _written, notes in ours["sessions"]:
            state = json.loads(state)
            seen["connected"] += state["state"] == "connected"
            seen["silent"] += about is None
            seen["theme"] += bool(state.get("theme"))
            seen["themes"] += bool(state.get("themes"))
            seen["turn"] += sum("theme" in line for line in notes) > 1
        assert len(ours["sessions"]) == len(sessions)
    assert all(count >= 5 for count in seen.values()), seen


@pytest.mark.kept
def test_the_demo_is_made_up_as_demo_py_makes_it_up() -> None:
    """The Python app's sensors/demo.py's demo_snapshot at fixed times (kept: golden.py): every
    reading in its order (the waves, the drives and programs, the sun, the song, the
    appointments, the forecast, the report of a round, a round under way, a download) with its
    unit, label and the reading it repeats, and the history of each wave; a first round's
    report too."""
    from libre_panel import i18n
    from libre_panel.fonts import FONT_DIR

    rng = random.Random(0xDE30)
    times = [0.0, 1.0, 1_700_000_000.0, 1_794_330_061.5, 86399.999, 951_782_400.25]
    times += [rng.uniform(1.6e9, 1.9e9) for _ in range(12)]
    if os.name == "nt":  # (the C runtime gives no local time before 1970: Python failed there)
        times = [t for t in times if t > 1e6]
    samples = 40
    for language in ("en", "de"):
        [ours] = run([{"op": "demo", "locale": str(i18n.LOCALE_DIR), "language": language,
                       "times": times, "samples": samples}])  # fmt: skip
        assert "snapshots" in ours, ours
        assert len(ours["snapshots"]) == len(times)
        for got in ours["snapshots"]:
            assert len(got["readings"]) > 400 and got["first"]
            assert all(len(values) == samples for _key, values in got["history"])
    # the pictures of a preview: the album's cover, the player's icon, the game's logo
    [ours] = run([{"op": "demo", "locale": str(i18n.LOCALE_DIR), "language": "en", "times": [],
                   "fonts": str(FONT_DIR)}])  # fmt: skip
    assert len(ours["pictures"]) == 3


@pytest.mark.kept
def test_serial_panels_are_found_among_the_ports_as_serial_link_py_finds_them() -> None:
    """The Python app's devices/serial_link.py (kept: golden.py): the port of a serial panel by
    its USB ids and serial number, for a model, for any and for a port that is named; the
    panels no panel has open; the ports with a gadget's ids, which are left alone unasked.
    Lists of ports made up of the ids and serial numbers the models have, of a gadget's and of
    others'."""
    from libre_panel import i18n
    from libre_panel.devices.models import MODELS

    rng = random.Random(41)
    # (the panels that talk over a serial port; a USB gadget's ids and its serial number)
    protocols = ("serial-a", "serial-b", "serial-c", "serial-d", "serial-weact",
                 "serial-jonsbo", "serial-hongtai")  # fmt: skip
    gadget_ids = {(0x0525, 0xA4A7), (0x1D6B, 0x0121), (0x1D6B, 0x0106)}
    serial = [m for m in MODELS if m.protocol in protocols]
    ids = sorted({pair for m in MODELS for pair in m.usb_ids} | gadget_ids)
    numbers = sorted({n for m in MODELS for n in m.serial_numbers})
    numbers += ["20080411", "other", ""]
    cases = []
    for _ in range(600):
        ports = []
        for n in rng.sample(range(6), rng.randrange(0, 5)):
            other = rng.choice([(1, 2), (None, None)])
            vid, pid = rng.choice(ids) if rng.random() < 0.85 else other
            number = rng.choice(numbers) if rng.random() < 0.7 else None
            ports.append([f"/dev/ttyACM{n}", vid, pid, number])
        model = rng.choice(serial).id if rng.random() < 0.6 else None
        if rng.random() < 0.1:
            model = rng.choice(MODELS).id
        port = f"/dev/ttyACM{rng.randrange(7)}" if rng.random() < 0.4 else ""
        cases.append({"ports": ports, "model": model, "port": port})
    case = {"op": "ports", "locale": str(i18n.LOCALE_DIR), "language": "de", "lists": cases}
    [ours] = run([case])
    # (of each list: the port found or why none, the panels free, the gadgets' ports)
    assert len(ours) == len(cases)
    assert sum(1 for found, _, _ in ours if isinstance(found, dict)) > 20
    assert sum(1 for found, _, _ in ours if isinstance(found, list)) > 150
    assert sum(1 for _, _, gadgets in ours if gadgets) > 20
