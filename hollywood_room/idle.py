"""Debounced busy-to-idle transitions; these are status events, not human questions."""

import socket
import time


def busy_teams(store, active=()):
    teams = {
        row["team"]
        for row in store.rows("""SELECT DISTINCT t.team FROM task t
        JOIN delivery d ON d.task=t.id
        WHERE d.state IN ('queued','dispatching','uncertain','waiting_usage','blocked')""")
    }
    teams.update(store.task_team(task) for task, _ in active)
    teams.update(
        row["team"]
        for row in store.rows("""SELECT DISTINCT t.team FROM task t
        JOIN binding b ON b.task=t.id WHERE b.status='working'""")
    )
    return teams


class Idle:
    def __init__(self, store, *, clock=time.monotonic, debounce=5, host=None):
        self.store, self.clock, self.debounce = store, clock, debounce
        self.host = host or socket.gethostname()
        self.armed = set()
        self.since = {}

    def sample(self, active=()):
        busy = busy_teams(self.store, active)
        self.armed.update(busy)
        for team in busy:
            self.since.pop(team, None)
        for team in self.armed - busy:
            started = self.since.setdefault(team, self.clock())
            if self.clock() - started < self.debounce:
                continue
            rows = self.store.rows("SELECT name,lobby FROM team WHERE id=?", (team,))
            if rows:
                row = rows[0]
                with self.store.db:
                    event = self.store.event(
                        "teamIdle",
                        row["lobby"],
                        "hollywood",
                        f"{self.host} · {row['name']} is ready for work",
                        {"team_id": team, "teamName": row["name"], "hostName": self.host},
                    )
                    self.store.db.execute("INSERT INTO push_outbox(event) VALUES(?)", (event,))
            self.since.pop(team, None)
            self.armed.remove(team)

    def disconnected(self):
        # Never finish a pre-disconnect debounce from stale in-memory activity.
        self.armed.clear()
        self.since.clear()


def stale(store, event):
    if event["kind"] != "teamIdle":
        return False
    team = store.task_team(event["task"])
    if team in busy_teams(store):
        return True
    # An idle push delayed by provider backoff must not survive a new busy cycle,
    # even when that cycle has already finished by the next provider attempt.
    return bool(
        store.rows(
            """SELECT d.id FROM delivery d JOIN task t ON t.id=d.task
        WHERE t.team=? AND d.event>? LIMIT 1""",
            (team, event["id"]),
        )
    )
