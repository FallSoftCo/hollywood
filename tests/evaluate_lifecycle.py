"""Real-model proof of TUI rebuild, mid-turn steering and persisted context recovery.

This Linux acceptance fixture uses sleep and disposable Git worktrees. It is run
explicitly, with the actual Losangelex client supplied by --client-source.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from aiohttp import web

from hollywood_room.models import Message
from hollywood_room.runtime import Runtime
from hollywood_room.server import Service, application
from hollywood_room.store import Store

LOGGER = logging.getLogger(__name__)


async def until(predicate, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.25)
    raise TimeoutError("Acceptance condition did not become true")


async def evaluate(args):
    sys.path.insert(0, str(args.client_source.resolve()))
    from losangelex_room.client import Room
    from textual.widgets import TextArea

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "room.db")
    runtime_args = {
        "binary": str(args.binary.resolve()),
        "home": args.codex_home,
        "state": root / "runtime",
        "workspace": args.workspace,
        "model": args.model,
    }
    runtime = Runtime(store, **runtime_args)
    service = Service(store, "lifecycle-fixture", runtime)
    runner = web.AppRunner(application(service))
    checks, errors, observations = {}, [], {}
    try:
        await runtime.start()
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        client = Room(url, "lifecycle-fixture", draft_path=root / "drafts.json")
        async with client.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            client.query_one("#composer", TextArea).load_text(
                "Maya, this is a Linux acceptance test in your isolated worktree. Remember the phrase "
                "CEDAR-481 for a later conversation. First run the shell command sleep 25; after it "
                "finishes create client-rebuild-check.txt containing BEFORE followed by a newline. "
                "Do not edit other files or create commits. Publish the resulting file path in the room."
            )
            await client.action_send().wait()
            await until(
                lambda: any(
                    e["kind"] == "artifact" and "sleep 25" in json.dumps(e["data"])
                    for e in store.events()
                )
            )
            before = store.snapshot()["agents"][0]
            observations["before"] = {
                "pid": runtime.rpc.process.pid,
                "thread": before["thread"],
                "turn": before["turn"],
            }
            client.query_one("#composer", TextArea).load_text(
                "Maya, update the acceptance test: the file must contain AFTER followed by a newline. "
                "Apply this correction after the sleep, retaining the same file and task."
            )
            await client.action_send().wait()
            await until(
                lambda: len(store.rows("SELECT id FROM delivery WHERE state='confirmed'")) >= 2
            )
            deliveries = store.rows("SELECT turn FROM delivery ORDER BY id")
            checks["redirect_uses_same_live_turn"] = (
                deliveries[0]["turn"] == deliveries[1]["turn"] == before["turn"]
            )
        # There is no TUI connected while its distributable is rebuilt.
        with (root / "client-build.log").open("wb") as log:
            build = await asyncio.create_subprocess_exec(
                "uv",
                "build",
                "--wheel",
                "--out-dir",
                str(root / "client-dist"),
                cwd=args.client_source,
                stdout=log,
                stderr=log,
            )
            checks["client_rebuild_succeeds"] = await build.wait() == 0
        during = store.snapshot()["agents"][0]
        observations["afterClientRebuild"] = {
            "pid": runtime.rpc.process.pid,
            "thread": during["thread"],
            "turn": during["turn"],
        }
        checks["rebuild_preserves_live_process_thread_and_turn"] = (
            observations["before"] == observations["afterClientRebuild"]
        )
        restored = Room(url, "lifecycle-fixture", draft_path=root / "drafts.json")
        async with restored.run_test(size=(100, 32)) as pilot:
            await until(lambda: not runtime.active and not runtime.dispatchers)
            await until(lambda: restored.cursor == store.snapshot()["cursor"])
            checks["restarted_tui_replays_completion"] = any(
                e["author"] == "maya" and e["kind"] == "message" for e in restored.events
            )
            await pilot.pause()
            restored.save_screenshot(str(root / "reconnected-tui.svg"))
        artifact = root / "runtime/workspaces/lobby/maya/client-rebuild-check.txt"
        checks["model_applies_mid_turn_correction"] = artifact.read_text() == "AFTER\n"
        original_thread = store.snapshot()["agents"][0]["thread"]
        await runner.cleanup()
        await runtime.close()
        store.close()
        # A host restart is a separate, deliberate operation after the turn is idle.
        store = Store(root / "room.db")
        runtime = Runtime(store, **runtime_args)
        service = Service(store, "lifecycle-fixture", runtime)
        await runtime.start()
        event = service.post(
            Message(
                commandId="after-runtime-restart",
                targets=["maya"],
                body="What exact phrase did I ask you to remember before the rebuild test? Reply briefly; do not read files or invoke shell commands.",
            )
        )
        await until(
            lambda: bool(
                store.rows(
                    "SELECT id FROM delivery WHERE event=? AND state='confirmed'", (event["id"],)
                )
            )
        )
        await until(lambda: not runtime.active and not runtime.dispatchers)
        checks["idle_runtime_restart_resumes_same_context"] = (
            store.snapshot()["agents"][0]["thread"] == original_thread
        )
        checks["model_remembers_prior_conversation"] = any(
            e["author"] == "maya" and "CEDAR-481" in e["body"]
            for e in store.events(after=event["id"])
        )
    except Exception as exc:
        LOGGER.exception("Lifecycle evaluation failed")
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await runner.cleanup()
        await runtime.close()
        report = {
            "model": args.model,
            "binarySha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
            "checks": checks,
            "errors": errors,
            "observations": observations,
            "transcript": store.events(limit=100),
            "snapshot": store.snapshot(),
        }
        (root / "report.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {"checks": checks, "errors": errors, "report": str(root / "report.json")}, indent=2
            ),
            flush=True,
        )
        store.close()
    if errors or not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "codex-home", "workspace", "output", "client-source"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-astra")
    asyncio.run(evaluate(parser.parse_args()))
