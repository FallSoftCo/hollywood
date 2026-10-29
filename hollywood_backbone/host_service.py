"""Persistent host adapter; no model runtime is created by this process.

Existing local NextComp and Hollywood stores retain execution, credentials and
receipts. Reconnection restores subscriptions, never resubmits operations.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import random
import signal
import stat
import time

from .backplane import HostBackplane, connect_host, identity
from .host_backend import NextCompReadAdapter


class HostService:
    def __init__(self, config, *, connection_factory=connect_host):
        if type(config.get("version")) is not int or config["version"] != 1:
            raise ValueError("Unsupported host service configuration")
        self.config = config
        self.authority_id = identity(config["authorityId"])
        self.host_id = identity(config["hostId"])
        source_admission = config.get("sourceAdmission", "configured-peers")
        if source_admission == "configured-peers":
            self.allowed = {identity(value) for value in config["authorizedHosts"]}
        elif source_admission == "broker-enrollment":
            self.allowed = None
        else:
            raise ValueError("Unknown source admission policy")
        self.connection_factory = connection_factory
        self.stopping = asyncio.Event()
        self.changed = asyncio.Event()
        self.state = {"version": 1, "state": "starting", "hostId": self.host_id,
                      "authorityId": self.authority_id, "capabilities": []}
        self.transport = None
        self.connection = None
        adapter = NextCompReadAdapter(config["localBackend"], config["localCredentialFile"])
        self.handlers = {**adapter.handlers(), "host.describe": self.describe}
        self.store = None
        if config.get("canonicalDatabaseConfig"):
            from .store import Store
            from .backplane_auth import AgentBindings
            from .backplane_rooms import AgentRoomTransport
            database = private_json(config["canonicalDatabaseConfig"])
            self.store = Store(database)
            with self.store.pool.connection() as connection:
                if not connection.execute("SELECT to_regclass('hb_backplane_agent_binding') AS binding").fetchone()["binding"]:
                    self.store.close()
                    raise ValueError("Canonical backplane enrollment migration required")
            self.handlers.update(AgentRoomTransport(AgentBindings(self.store)).handlers())
        self.state["capabilities"] = sorted(self.handlers)
        path = Path(config["stateFile"])
        if not path.is_absolute() or not path.parent.is_dir() or path.parent.stat().st_mode & 0o022:
            if self.store:
                self.store.close()
            raise ValueError("Private absolute host state directory required")
        descriptor = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock = os.fdopen(descriptor, "a+")
        except BaseException:
            os.close(descriptor)
            if self.store:
                self.store.close()
            raise

    def publish_state(self, state, error=None):
        self.state = {**self.state, "state": state, "observedAt": int(time.time() * 1000),
                      "error": error}
        destination = self.config.get("stateFile")
        if destination:
            path = Path(destination)
            temporary = path.with_name(path.name + ".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                                 | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "w") as output:
                json.dump(self.state, output)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        self.changed.set()

    async def describe(self, request):
        if request.payload:
            from .backplane import BackplaneError
            raise BackplaneError("INVALID_PARAMETERS")
        return self.state.copy()

    async def run(self):
        failures = 0
        try:
            while not self.stopping.is_set():
                closed = asyncio.Event()
                async def on_closed():
                    closed.set()
                async def on_disconnected():
                    if not self.stopping.is_set():
                        self.publish_state("unavailable", "NATS_DISCONNECTED")
                async def on_reconnected():
                    if not self.stopping.is_set():
                        self.publish_state("ready")
                try:
                    self.publish_state("connecting")
                    self.connection = await self.connection_factory(
                        server=self.config["server"], host_id=self.host_id,
                        password=self.config["password"], ca_file=self.config["caFile"],
                        timeout=8, closed_cb=on_closed,
                        disconnected_cb=on_disconnected, reconnected_cb=on_reconnected)
                    self.transport = HostBackplane(
                        self.connection, authority_id=self.authority_id, host_id=self.host_id,
                        handlers=self.handlers, authorized_sources=self.allowed,
                        control_operations=frozenset({"host.describe"}))
                    await self.transport.start()
                    self.publish_state("ready")
                    connected_at = time.monotonic()
                    stop_wait = asyncio.create_task(self.stopping.wait())
                    close_wait = asyncio.create_task(closed.wait())
                    try:
                        await asyncio.wait((stop_wait, close_wait), return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in (stop_wait, close_wait):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(stop_wait, close_wait, return_exceptions=True)
                    self.publish_state("draining" if self.stopping.is_set() else "unavailable",
                                       None if self.stopping.is_set() else "NATS_DISCONNECTED")
                    if time.monotonic() - connected_at >= 60:
                        failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self.publish_state("unavailable", type(error).__name__)
                finally:
                    try:
                        if self.transport:
                            await self.transport.close()
                    finally:
                        self.transport = None
                        if self.connection:
                            await self.connection.close()
                            self.connection = None
                if self.stopping.is_set():
                    break
                failures += 1
                self.publish_state("waiting_for_connection", self.state.get("error"))
                delay = min(300, 2 ** min(failures, 9)) * random.uniform(.8, 1.2)
                try:
                    await asyncio.wait_for(self.stopping.wait(), delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            try:
                self.publish_state("stopped")
                if self.store:
                    self.store.close()
            finally:
                self.lock.close()

    def stop(self):
        self.stopping.set()


def private_json(path):
    path = Path(path)
    info = path.stat()
    if (not path.is_absolute() or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077 or (os.geteuid() != 0 and info.st_uid != os.geteuid())):
        raise PermissionError("Private host configuration required")
    return json.loads(path.read_text())


async def serve(path):
    service = HostService(private_json(path))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, service.stop)
    await service.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.config))


if __name__ == "__main__":
    main()
