import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import TypedDict

from vpn_manager.config import settings
from vpn_manager.http import APIError, request
from vpn_manager.messaging import alerts
from vpn_manager.messaging.models import MonitorSummary, ProbeSummary

from .probes import load_config, probe_check


class CheckRow(TypedDict):
    probe: str
    name: str
    failures: int
    down: int
    checked_at: str
    detail: str


class MonitorStatus(TypedDict):
    checks: list[CheckRow]
    pending: dict[str, int]


def open_state(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS checks (
            probe TEXT, name TEXT, failures INTEGER, down INTEGER,
            checked_at TEXT, detail TEXT, PRIMARY KEY (probe, name)
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY, channel TEXT, message TEXT, created_at TEXT
        );
    """)

    return connection


def record(connection, probe, threshold, results, channels, now):
    connection.execute("BEGIN IMMEDIATE")

    with connection:
        for name, healthy, detail in results:
            previous = connection.execute(
                "SELECT failures, down FROM checks WHERE probe=? AND name=?",
                (probe, name),
            ).fetchone() or (0, False)
            failures = 0 if healthy else min(previous[0] + 1, threshold)
            down = not healthy and (bool(previous[1]) or failures >= threshold)
            connection.execute(
                """
                INSERT INTO checks VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (probe, name) DO UPDATE SET failures=excluded.failures,
                    down=excluded.down, checked_at=excluded.checked_at, detail=excluded.detail
            """,
                (probe, name, failures, down, now, detail),
            )

            if down != bool(previous[1]):
                event = "Сбой" if down else "Восстановлено"
                message = f"{event} | {probe} / {name}\n{detail}\n{now}"
                connection.executemany(
                    "INSERT INTO outbox (channel, message, created_at) VALUES (?, ?, ?)",
                    ((channel, message, now) for channel in channels),
                )


def deliver(connection, channels):
    failed = False

    for channel in channels:
        rows = connection.execute(
            "SELECT id, message FROM outbox WHERE channel=? ORDER BY id LIMIT 5",
            (channel,),
        ).fetchall()

        for index, (identifier, message) in enumerate(rows):
            if index and channel == "max":
                sleep(0.55)

            try:
                alerts.send(channel, message)
            except APIError as error:
                print(f"{channel}: {error}", file=sys.stderr)
                failed = True
                break

            with connection:
                connection.execute("DELETE FROM outbox WHERE id=?", (identifier,))

    return failed


def run(config_path, state_path, dry_run, *, report=None):
    probe, threshold, checks = load_config(config_path)
    channels = [] if dry_run else alerts.configured_channels()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [
            (check.name, *result) for check, result in zip(checks, pool.map(probe_check, checks))
        ]

    output = {"probe": probe, "time": now, "results": results}

    if report is None:
        print(json.dumps(output, ensure_ascii=False))
    else:
        report(output)

    if dry_run:
        return int(any(not healthy for _, healthy, _ in results))

    with closing(open_state(state_path)) as connection:
        record(connection, probe, threshold, results, channels, now)
        failed = deliver(connection, channels)

    if heartbeat := settings.get("VPN_MANAGER_HEARTBEAT_URL"):
        try:
            request(heartbeat, expect_json=False)
        except APIError as error:
            print(f"heartbeat: {error}", file=sys.stderr)
            failed = True

    return int(failed)


def status(path: Path) -> MonitorStatus:
    if not path.is_file():
        raise ValueError("Нет сохранённых проверок")

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM checks ORDER BY probe, name").fetchall()
        queue = connection.execute(
            "SELECT channel, count(*) FROM outbox GROUP BY channel"
        ).fetchall()

    checks = [
        CheckRow(
            probe=row["probe"],
            name=row["name"],
            failures=row["failures"],
            down=row["down"],
            checked_at=row["checked_at"],
            detail=row["detail"],
        )
        for row in rows
    ]

    return {"checks": checks, "pending": dict(queue)}


class StoredChecks:
    def __init__(self, config: Path, state: Path, max_age: int):
        self.probe, _, checks = load_config(config)
        self.checks = {check.name: check for check in checks}
        self.public = {check.name: check for check in checks if check.public}
        self.state = state
        self.max_age = max_age
        self.recent: dict[str, tuple[float, bool]] = {}
        self.lock = Lock()

    def names(self, *, include_private: bool = False) -> tuple[str, ...]:
        return tuple(self.checks if include_private else self.public)

    def ping(self, name: str, *, include_private: bool = False) -> bool:
        if name not in self.names(include_private=include_private):
            raise ValueError("Неизвестная публичная проверка")

        with self.lock:
            recent = self.recent.get(name)

            if recent and monotonic() - recent[0] < 30:
                return recent[1]

            result, _ = probe_check(self.checks[name])
            self.recent[name] = monotonic(), result

        return result

    def status(self, *, detailed: bool) -> MonitorSummary:
        if not self.state.is_file():
            return MonitorSummary(())

        try:
            snapshot = status(self.state)
        except sqlite3.Error as error:
            raise RuntimeError("Не удалось прочитать состояние мониторинга") from error

        checks = []

        for row in snapshot["checks"]:
            if row["probe"] != self.probe or (not detailed and row["name"] not in self.public):
                continue

            status_label = "доступен"
            checked_at = datetime.fromisoformat(row["checked_at"])
            age = (datetime.now(timezone.utc) - checked_at).total_seconds()

            if age > self.max_age:
                status_label = "данные устарели"
            elif row["down"]:
                status_label = "сбой"
            elif row["failures"]:
                status_label = "нестабильно"

            checks.append(ProbeSummary(row["name"], status_label, row["checked_at"]))

        pending = tuple(snapshot["pending"].items()) if detailed else ()

        return MonitorSummary(tuple(checks), pending)
