"""Real-model proof of a development root, nested repositories and resumed contexts.

Snapshots the actual room documentation into disposable repositories. No model may
edit the source checkouts. Run explicitly; it is outside the deterministic suite.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path

from evaluate_room import settle

from hollywood_room.models import Message
from hollywood_room.runtime import Runtime
from hollywood_room.server import Service
from hollywood_room.store import Store

LOGGER = logging.getLogger(__name__)


async def evaluate(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "Development"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "Use the repository subfolders named in the request. Read each subfolder's "
        "AGENTS.md before editing. Preserve existing changes.\n"
    )
    fixtures = (
        ("hollywood-next", "ROOM.md", args.hollywood_source / "ROOM.md", "HOLLYWOOD-CHECK"),
        (
            "losangelex-next",
            "losangelex/ROOM.md",
            args.losangelex_source / "losangelex/ROOM.md",
            "LOSANGELEX-CHECK",
        ),
    )
    originals = {}
    for name, relative, source, marker in fixtures:
        repo = workspace / name
        target = repo / relative
        target.parent.mkdir(parents=True)
        shutil.copyfile(source, target)
        originals[str(target.relative_to(workspace))] = target.read_text()
        (target.parent / "AGENTS.md").write_text(
            f"This is a frozen documentation fixture. When asked to update {target.name}, "
            f"append exactly `{marker}` on a new line. Do not change any other file.\n"
        )
        for command in (
            ["git", "init", "-q"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "Freeze room documentation",
            ],
        ):
            process = await asyncio.create_subprocess_exec(*command, cwd=repo)
            if await process.wait():
                raise RuntimeError("Could not initialize fixture repository")
        (repo / "existing-user-work.txt").write_text("Preserve this uncommitted file.\n")
    state = root / "runtime"
    store = Store(root / "room.db")
    common = {
        "binary": str(args.binary.resolve()),
        "home": args.codex_home,
        "state": state,
        "model": args.model,
    }
    runtime = Runtime(store, workspace=workspace / "hollywood-next", **common)
    checks, errors, observations = {}, [], {}
    started = time.monotonic()
    try:
        # Establish a real persisted thread under the old per-repository worktree mode.
        await runtime.start()
        service = Service(store, "fixture", runtime)
        service.post(
            Message(
                commandId="remember",
                targets=["maya"],
                body="Maya, this is a workspace migration acceptance test. Remember the phrase MAPLE-582 "
                "for later. Reply briefly. Do not read or write files or invoke commands.",
            )
        )
        await settle(runtime)
        original = store.snapshot()["agents"][0]["thread"]
        assert original
        previous = await runtime.rpc.call("thread/read", {"threadId": original})
        observations["oldCwd"] = previous["thread"]["cwd"]
        await runtime.close()

        # The same durable room and thread now use a non-Git development directory.
        runtime = Runtime(store, workspace=workspace, workspace_mode="shared", **common)
        service = Service(store, "fixture", runtime)
        await runtime.start()
        request = service.post(
            Message(
                commandId="subfolders",
                targets=["maya"],
                body="Maya, the project root is now the whole Development folder, including its subfolders. "
                "For this frozen acceptance test, update hollywood-next/ROOM.md and "
                "losangelex-next/losangelex/ROOM.md according to the instructions alongside each file. "
                "Keep their original content and append only the required line. Preserve all other "
                "files and existing changes. Do not commit. Report the earlier remembered phrase and "
                "the two actual paths you changed after checking them.",
            )
        )
        await settle(runtime, timeout=300)
        bindings = store.snapshot()["agents"]
        current = await runtime.rpc.call("thread/read", {"threadId": original})
        observations["newCwd"] = current["thread"]["cwd"]
        checks["sameThreadAfterRootChange"] = bindings[0]["thread"] == original
        checks["resumedThreadUsesDevelopmentRoot"] = current["thread"]["cwd"] == str(workspace)
        checks["priorConversationRemembered"] = any(
            e["author"] == "maya" and "MAPLE-582" in e["body"]
            for e in store.events(after=request["id"])
        )
        for name, relative, _, marker in fixtures:
            target = workspace / name / relative
            before = originals[str(target.relative_to(workspace))]
            checks[f"{name}InstructionsAndEdit"] = (
                target.read_text() == before.rstrip("\n") + "\n" + marker + "\n"
            )
            checks[f"{name}ExistingWorkPreserved"] = (
                workspace / name / "existing-user-work.txt"
            ).read_text() == "Preserve this uncommitted file.\n"
        checks["noApprovalForSubfolderEdits"] = not store.rows(
            "SELECT id FROM event WHERE kind='approval'"
        )

        # A newly delegated teammate must select the same nested repository from its brief.
        delegated = service.post(
            Message(
                commandId="delegate",
                body="Create the task shared-repo-review and delegate to Theo using hollywood_assign: "
                "review losangelex-next/losangelex/ROOM.md in the shared Development root. Read the "
                "actual file, report its last non-empty line and absolute path, and do not edit it. "
                "Do not do the review yourself.",
            )
        )
        await settle(runtime, timeout=300)
        workers = [
            b
            for b in store.snapshot()["agents"]
            if b["task"] == "shared-repo-review" and b["agent"] == "theo"
        ]
        checks["delegatedIndependentThread"] = bool(workers) and workers[0]["thread"] != original
        checks["delegationSelectsNestedRepository"] = any(
            e["author"] == "theo"
            and "LOSANGELEX-CHECK" in e["body"]
            and str(workspace / "losangelex-next/losangelex/ROOM.md") in e["body"]
            for e in store.events(after=delegated["id"])
        )
        checks["noSourceWorktreeWasRemoved"] = Path(observations["oldCwd"]).is_dir()
    except Exception as exc:
        LOGGER.exception("Shared workspace evaluation failed")
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        await runtime.close()
        transcript, cursor = [], 0
        while batch := store.events(after=cursor):
            transcript.extend(batch)
            cursor = batch[-1]["id"]
        report = {
            "model": args.model,
            "binarySha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
            "elapsedSeconds": round(time.monotonic() - started, 2),
            "checks": checks,
            "errors": errors,
            "observations": observations,
            "snapshot": store.snapshot(),
            "transcript": transcript,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
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
    parser.add_argument("--hollywood-source", type=Path, required=True)
    parser.add_argument("--losangelex-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-astra")
    asyncio.run(evaluate(parser.parse_args()))
