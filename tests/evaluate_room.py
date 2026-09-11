"""Live-model acceptance run. Uses real Codex roots and frozen user scenarios.

Run explicitly; this is deliberately outside the deterministic pytest suite.
All work happens in disposable Git worktrees; the report contains no credentials.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

from aiohttp import ClientSession, web

from hollywood_room.models import Message
from hollywood_room.runtime import Runtime
from hollywood_room.server import Service, application
from hollywood_room.store import Store

LOGGER = logging.getLogger(__name__)


async def settle(runtime, *, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        waiting = runtime.store.rows(
            "SELECT id FROM delivery WHERE state IN ('queued','dispatching')"
        )
        if not waiting and not runtime.active and not runtime.dispatchers:
            return
    raise TimeoutError("Team did not settle within the scenario deadline")


async def evaluate(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "room.db")
    runtime = Runtime(
        store,
        binary=str(args.binary.resolve()),
        home=args.codex_home,
        state=root / "runtime",
        workspace=args.workspace,
        model=args.model,
        ephemeral=True,
    )
    service = Service(store, "evaluation-only-token", runtime)
    runner = web.AppRunner(application(service))
    checks, errors = {}, []
    start = time.monotonic()
    try:
        await runtime.start()
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        headers = {"Authorization": "Bearer evaluation-only-token"}
        # Frozen from the first real room run: a direct request caused three redundant
        # Maya messages before Theo answered. Reuse the exact request as the regression.
        prompt = "Maya, you are participating in an isolated Hollywood integration test. Use hollywood_say to ask Theo to reply with his role. Keep it brief. Do not write files or invoke shell commands."
        async with (
            ClientSession(headers=headers) as client,
            client.post(
                url + "/hollywood/v2/messages", json={"commandId": "handoff", "body": prompt}
            ) as response,
        ):
            assert response.status == 202
            submitted = await response.json()
        # Every HTTP client is now gone. The service must finish both model turns.
        await settle(runtime)
        timeline = store.events(limit=100)
        shared = [e for e in timeline if e["kind"] == "message" and e["author"] != "you"]
        checks["direct_address"] = any(e["author"] == "maya" for e in shared)
        checks["peer_wake_and_reply"] = any(e["author"] == "theo" for e in shared)
        checks["bounded_chatter"] = len([e for e in shared if e["author"] == "maya"]) == 1
        checks["finished_without_client"] = all(
            a["status"] == "idle" for a in store.snapshot()["agents"]
        )
        async with ClientSession(headers=headers) as client:
            async with client.get(url + "/hollywood/v2/messages?after=0") as response:
                checks["reconnect_replays_history"] = (await response.json())["data"] == timeline
            async with client.post(
                url + "/hollywood/v2/messages", json={"commandId": "handoff", "body": prompt}
            ) as response:
                checks["reconnect_send_is_idempotent"] = await response.json() == submitted

        # Frozen from the user's accepted product direction: task-specific teammate
        # contexts and private steering, with a brief sent to the coordinator.
        store.add_task("notifications", "Android notifications", submitted["id"])
        private = service.post(
            Message(
                commandId="private",
                task="notifications",
                targets=["maya"],
                visibility="direct",
                body="For this Android notification task, use generic lock-screen text and fetch task details only when the app opens. Share this committed decision with the coordinator using hollywood_brief. Keep the private diagnostic label ORCHID-739 out of that brief and any public messages. Do not edit files.",
            )
        )
        await settle(runtime)
        rows = store.events(after=private["id"], task="notifications", limit=100)
        briefs = [
            e
            for e in rows
            if e["author"] == "maya"
            and e["visibility"] == "room"
            and e["data"].get("targets") == ["coordinator"]
        ]
        checks["private_task_brief"] = bool(briefs) and all(
            "ORCHID-739" not in e["body"] for e in rows if e["visibility"] == "room"
        )
        bindings = store.snapshot()["agents"]
        maya_threads = [b["thread"] for b in bindings if b["agent"] == "maya"]
        checks["separate_context_per_task"] = len(maya_threads) == 2 and len(set(maya_threads)) == 2

        service.post(
            Message(
                commandId="question",
                task="notifications",
                targets=["rowan"],
                body="Rowan, review our Android notification requirement. Before continuing, ask me via hollywood_ask whether completion alerts should also be generic. This is a real UI acceptance scenario; do not write files.",
            )
        )
        await settle(runtime)
        attention = store.snapshot()["attention"]
        checks["durable_question"] = any(a["agent"] == "rowan" for a in attention)
        if checks["durable_question"]:
            question = next(a for a in attention if a["agent"] == "rowan")
            async with (
                ClientSession(headers=headers) as client,
                client.post(
                    url + f"/hollywood/v2/attention/{question['id']}/answer",
                    json={
                        "commandId": "answer-question",
                        "body": "Yes, completion alerts should also be generic. Confirm the updated requirement.",
                    },
                ) as response,
            ):
                checks["answer_routes_to_agent"] = response.status == 200
            await settle(runtime)
            checks["question_resolved"] = not store.snapshot()["attention"]
            checks["push_enqueued_without_client"] = bool(
                store.rows("SELECT * FROM push_outbox WHERE event=?", (question["id"],))
            )
        threads = [b["thread"] for b in store.snapshot()["agents"]]
        checks["all_contexts_independent"] = (
            len(threads) == len(set(threads)) and None not in threads
        )
        # Real failed delegation captured in the first artifact run: copying an instruction
        # addressed to the coordinator made Maya interpret "do not write it yourself" as
        # a prohibition on her assignment. Keep the original prompt unchanged.
        artifact_request = service.post(
            Message(
                commandId="artifact",
                body='Create the task notification-check. Delegate to Maya using hollywood_assign: write a new file notification-contract.txt containing exactly "generic lock-screen text; fetch details on open" followed by a newline. This is an isolated acceptance test in your own task worktree. Do not change any other files or create a commit. Ask her to report the absolute path in the room when done. Track the assignment; do not write the file yourself.',
            )
        )
        await settle(runtime, timeout=300)
        paths = list((root / "runtime/workspaces").glob("*/maya/notification-contract.txt"))
        checks["delegated_artifact_correct"] = bool(paths) and all(
            path.read_text() == "generic lock-screen text; fetch details on open\n"
            for path in paths
        )
        checks["followups_use_correct_task_context"] = not store.rows(
            "SELECT id FROM delivery WHERE event>? AND agent='maya' AND task='lobby'",
            (artifact_request["id"],),
        )
    except Exception as exc:
        LOGGER.exception("Live evaluation failed")
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await runner.cleanup()
        await runtime.close()
        transcript = []
        cursor = 0
        while batch := store.events(after=cursor):
            transcript.extend(batch)
            cursor = batch[-1]["id"]
        report = {
            "model": args.model,
            "binary": str(args.binary.resolve()),
            "binarySha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
            "elapsedSeconds": round(time.monotonic() - start, 2),
            "checks": checks,
            "errors": errors,
            "snapshot": store.snapshot(),
            "transcript": transcript,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {"checks": checks, "errors": errors, "report": str(root / "report.json")}, indent=2
            ),
            flush=True,
        )
        store.close()
    if errors or not checks or not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-astra")
    asyncio.run(evaluate(parser.parse_args()))
