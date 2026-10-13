import asyncio
from unittest.mock import AsyncMock

import pytest

from hollywood_room.runtime import Runtime
from hollywood_room.store import Store
from hollywood_room.tools import instruction
from hollywood_room.workspace import SHARED_WORKSPACE_INSTRUCTIONS, Workspaces


async def test_shared_directory_supports_subfolders_without_creating_worktrees(tmp_path):
    source = tmp_path / "Development"
    nested = source / "project" / "src"
    nested.mkdir(parents=True)
    existing = nested / "existing.txt"
    existing.write_text("uncommitted user work")
    manager = Workspaces(source, tmp_path / "worktrees", mode="shared")

    assert [
        await manager.for_agent(task, agent)
        for task, agent in (("lobby", "coordinator"), ("task-a", "maya"), ("task-b", "theo"))
    ] == [source, source, source]
    assert existing.read_text() == "uncommitted user work"
    assert not (tmp_path / "worktrees").exists()


def test_missing_shared_root_fails_before_starting_an_agent(tmp_path):
    with pytest.raises(ValueError, match="Workspace directory does not exist"):
        Workspaces(tmp_path / "missing", tmp_path / "worktrees", mode="shared")


async def test_worktree_mode_still_preserves_independent_edits(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    for command in (
        ["git", "init", "-q"],
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Initial",
        ],
    ):
        process = await asyncio.create_subprocess_exec(*command, cwd=source)
        assert await process.wait() == 0
    manager = Workspaces(source, tmp_path / "worktrees")
    maya = await manager.for_agent("task-a", "maya")
    (maya / "existing.txt").write_text("unfinished work")
    theo = await manager.for_agent("task-a", "theo")
    assert await manager.for_agent("task-a", "maya") == maya
    assert (maya / "existing.txt").read_text() == "unfinished work"
    assert not (theo / "existing.txt").exists()
    assert not (source / "existing.txt").exists()


async def test_existing_thread_resumes_at_shared_root_without_changing_identity(tmp_path):
    store = Store(tmp_path / "room.db")
    try:
        store.ensure_binding("lobby", "maya")
        with store.db:
            store.db.execute("UPDATE binding SET thread='existing-thread'")
        runtime = Runtime(
            store,
            binary="unused",
            home=tmp_path,
            state=tmp_path,
            workspace=tmp_path,
            workspace_mode="shared",
        )
        runtime.rpc.call = AsyncMock(return_value={})
        assert await runtime.thread("lobby", "maya") == "existing-thread"
        assert await runtime.thread("lobby", "maya") == "existing-thread"
        runtime.rpc.call.assert_awaited_once_with(
            "thread/resume",
            {
                "threadId": "existing-thread",
                "excludeTurns": True,
                "cwd": str(tmp_path),
                "sandbox": "danger-full-access",
                "approvalPolicy": "never",
                "developerInstructions": instruction("maya", "lobby")
                + SHARED_WORKSPACE_INSTRUCTIONS,
                "config": {"features.multi_agent": False, "features.multi_agent_v2": False},
            },
        )
        assert store.rows("SELECT thread FROM binding") == [{"thread": "existing-thread"}]
    finally:
        store.close()
