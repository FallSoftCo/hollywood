"""Per-task worktrees keep independently running teammates' edits separate."""

import asyncio
from pathlib import Path


class Workspaces:
    def __init__(self, source: Path, root: Path):
        self.source, self.root = source, root
        self.lock = asyncio.Lock()

    async def for_agent(self, task, agent):
        target = self.root / task / agent
        async with self.lock:
            if (target / ".git").exists():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.source),
                "worktree",
                "add",
                "--detach",
                str(target),
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, error = await process.communicate()
            if process.returncode:
                raise ValueError("Could not create task worktree: " + error.decode()[:400])
        return target
