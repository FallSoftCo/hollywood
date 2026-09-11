"""Durably wake the authorized Codex maintainer through the existing room runtime."""

import argparse
import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

TASK = "codex-upstream-maintainer"


def brief(repo, day):
    return (
        f"Scheduled daily maintenance ({day}), authorized by human room request #3423. "
        "You are the persistent Losangelex Codex upstream maintainer. This is a scheduled "
        "invocation of that standing request. Continue any unfinished integration first. "
        f"Repository: {repo}. Read applicable AGENTS.md and inspect Git status. "
        "Fetch https://github.com/openai/codex.git main into refs/remotes/maintainer-upstream/main "
        "and record the upstream SHA, fork SHA, divergence and changed components. "
        "Use branch losangelex-next as the fork baseline, preserving every existing local "
        "change. Do integration only in a separate worktree under the maintainer state directory "
        "(~/.local/state/losangelex-maintainer/worktrees), on a named maintainer branch. "
        "Never reset, stash, clean, checkout or merge in the owner's active checkout. "
        "Reuse an unfinished candidate; do not accumulate a new worktree each day. "
        "Merge upstream, resolve conflicts while preserving Losangelex functionality, and "
        "run the repository's required targeted tests, schema checks and candidate build. "
        "Respect AGENTS.md approval requirements for full workspace tests. Check available "
        "disk space before builds; clean only disposable artifacts owned by this maintainer. "
        "Review CLI/config/session/app-server compatibility and the Hollywood integration. "
        "You may use teammates for bounded testing/review/build cleanup when helpful. "
        "Record exact commands, exit status, SHAs, remaining conflicts and candidate location "
        "in ~/.local/state/losangelex-maintainer/report.md. Commit verified integration to "
        "the isolated candidate branch; coordinate landing with the active fork owner only "
        "after validation and accounting for newer local commits. Never publish or deploy "
        "an unverified build. Do not report queued work as completed integration. "
        "Publish a concise result or concrete blocker in this task and notify coordinator. "
        "If no upstream changes exist, record that evidence. This task recurs daily; retain "
        "its state and report for the next invocation."
    )


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def tick(state, token_file, endpoint, repo, now):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger_path = state / "schedule.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        today = now.date().isoformat()
        pending = ledger.get("pending")
        if pending is None:
            if now.hour < 9 or ledger.get("delivered_day", "") >= today:
                return "not due"
            pending = {
                "commandId": f"codex-maintainer-daily-{today}",
                "task": TASK,
                "targets": ["maya"],
                "body": brief(repo, today),
            }
            ledger["pending"] = pending
            save(ledger_path, ledger)
        request = Request(
            endpoint.rstrip("/") + "/hollywood/v2/messages",
            data=json.dumps(pending).encode(),
            headers={
                "Authorization": "Bearer " + token_file.read_text().strip(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                event = json.load(response)
            if not isinstance(event.get("id"), int) or event.get("task") != TASK:
                raise ValueError("Room did not return a matching durable task event")
        except Exception as exc:
            ledger["last_error"] = f"{type(exc).__name__}: delivery failed"
            ledger["last_attempt"] = now.isoformat()
            save(ledger_path, ledger)
            raise
        ledger.update(
            delivered_day=pending["commandId"].removeprefix("codex-maintainer-daily-"),
            delivered_at=now.isoformat(),
            event=event["id"],
        )
        ledger.pop("pending", None)
        ledger.pop("last_error", None)
        save(ledger_path, ledger)
        return f"queued maintainer event {event['id']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18766")
    args = parser.parse_args()
    print(
        tick(
            args.state,
            args.token_file,
            args.endpoint,
            args.repo,
            datetime.now(ZoneInfo("America/New_York")),
        )
    )


if __name__ == "__main__":
    main()
