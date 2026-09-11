"""Choose isolated Git worktrees or an explicitly shared development directory."""

import asyncio
from pathlib import Path

SHARED_WORKSPACE_INSTRUCTIONS = (
    " Your working directory is the owner's shared development root, which can contain multiple "
    "repositories and ordinary folders. Work directly in the subfolder named by the task unless "
    "a separate worktree is requested. Before editing, read applicable AGENTS.md instructions "
    "from the development root through the target subfolder and inspect that repository's Git "
    "status. Include the selected repository and relative file paths in delegation briefs. "
    "Coordinate ownership of overlapping files before parallel edits and preserve existing changes. "
    "Separate conversations share the same files in this mode."
)


class Workspaces:
    def __init__(self, source: Path, root: Path, *, mode="worktree"):
        if mode not in ("worktree", "shared"):
            raise ValueError("Workspace mode must be worktree or shared")
        self.source, self.root, self.mode = source.resolve(), root.resolve(), mode
        if not self.source.is_dir():
            raise ValueError(f"Workspace directory does not exist: {self.source}")
        self.lock = asyncio.Lock()

    async def for_agent(self, task, agent):
        if self.mode == "shared":
            return self.source
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
