"""Bounded JSON-RPC transport over a private Codex app-server stdio connection."""

import asyncio
import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class RpcError(Exception):
    pass


class AppServer:
    def __init__(self, binary: str, home: Path, state: Path, on_event, on_request):
        self.binary, self.home, self.state = binary, home, state
        self.on_event, self.on_request = on_event, on_request
        self.pending = {}
        self.requests = set()
        self.sequence = 0
        self.process = None
        self.reader = None

    async def start(self):
        self.home.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, CODEX_HOME=str(self.home))
        # Pin the executable; inherited launcher overrides must not choose another build.
        for key in ("CODEX_CLI_PATH", "CODEX_EXECUTABLE", "CODEX_EXEC_SERVER_URL"):
            env.pop(key, None)
        with (self.state / "app-server.log").open("ab") as log:
            self.process = await asyncio.create_subprocess_exec(
                self.binary,
                "app-server",
                "--listen",
                "stdio://",
                "-c",
                f"sqlite_home={json.dumps(str(self.state / 'sqlite'))}",
                "-c",
                f"log_dir={json.dumps(str(self.state / 'logs'))}",
                "-c",
                "mcp_servers={}",
                "-c",
                "check_for_update_on_startup=false",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
                env=env,
                limit=16 * 1024 * 1024,
            )
        self.reader = asyncio.create_task(self._read())
        await self.call(
            "initialize",
            {
                "clientInfo": {"name": "hollywood-room", "version": "0.2.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.write({"method": "initialized", "params": {}})

    async def write(self, message):
        if self.process is None or self.process.returncode is not None:
            raise RpcError("Codex runtime is unavailable")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method, params, *, timeout=60):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _request(self, message):
        try:
            result = await self.on_request(message["method"], message.get("params", {}))
            await self.write({"id": message["id"], "result": result})
        except Exception as exc:
            LOGGER.exception("App-server request handler failed: %s", message["method"])
            await self.write(
                {
                    "id": message["id"],
                    "error": {
                        "code": -32603,
                        "message": f"Hollywood request failed: {type(exc).__name__}",
                    },
                }
            )

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        task = asyncio.create_task(self._request(message))
                        self.requests.add(task)
                        task.add_done_callback(self.requests.discard)
                    else:
                        await self.on_event(message["method"], message.get("params", {}))
                elif (future := self.pending.get(message.get("id"))) and not future.done():
                    if "error" in message:
                        future.set_exception(RpcError(str(message["error"])))
                    else:
                        future.set_result(message.get("result", {}))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RpcError("Codex connection lost; reconcile before retry"))
            await self.on_event("hollywood/runtimeLost", {})

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 15)
            except asyncio.TimeoutError:
                self.process.terminate()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        for task in self.requests:
            task.cancel()
        await asyncio.gather(*self.requests, return_exceptions=True)
