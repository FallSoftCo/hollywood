"""Real-broker qualification. No models, production state or SSH execution."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from uuid import uuid4

import nats
from nats.errors import TimeoutError as NATSTimeout
from hollywood_backbone.backplane import HostBackplane, BackplaneError, encode


async def qualify(binary, *, authority_id=None, configure=None, response_max=1, additional_authorities=()):
    with tempfile.TemporaryDirectory(prefix="hollywood-backplane-") as temporary:
        root = Path(temporary)
        with socket.socket() as port:
            port.bind(("127.0.0.1", 0))
            number = port.getsockname()[1]
        authority, cpu, gpu = [str(uuid4()) for _ in range(3)]
        authority = authority_id or authority
        passwords = {host: secrets.token_urlsafe(32) for host in (cpu, gpu)}
        prefix = f"hollywood.v1.{authority}"
        users = []
        foreign_reads = lambda host: [f"hollywood.v1.{a}.service.{host}.hollywood.catalog.read" for a in additional_authorities] + [f"hollywood.v1.{a}.service.{host}.hollywood.viewer.room.read" for a in additional_authorities] + [f"hollywood.v1.{a}.service.{host}.hollywood.agent.route" for a in additional_authorities] + [f"hollywood.v1.{a}.service.{host}.{op}" for a in additional_authorities for op in ("hollywood.agent.authenticate","hollywood.agent.domain")]
        foreign_subscriptions = [f"hollywood.v1.{a}.service.*.hollywood.catalog.read" for a in additional_authorities] + [f"hollywood.v1.{a}.service.*.hollywood.viewer.room.read" for a in additional_authorities] + [f"hollywood.v1.{a}.service.*.hollywood.agent.route" for a in additional_authorities] + [f"hollywood.v1.{a}.service.*.{op}" for a in additional_authorities for op in ("hollywood.agent.authenticate","hollywood.agent.domain")]
        for host in (cpu, gpu):
            users.append({"user": host, "password": passwords[host], "permissions": {
                "publish": {"allow": [f"{prefix}.rpc.{host}.*.*", f"{prefix}.service.{host}.>", "$SRV.INFO.hollywood", *foreign_reads(host)]},
                "subscribe": {"allow": [f"{prefix}.rpc.*.{host}.*", f"_INBOX.{host}.>", f"{prefix}.service.*.>", "$SRV.>", *foreign_subscriptions]},
                "allow_responses": {"max": response_max, "expires": "60s"}}})
        config = {"host": "127.0.0.1", "port": number, "max_payload": 524288,
                  "max_connections": 8, "authorization": {"users": users}}
        config_path = root / "nats.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        log = (root / "broker.log").open("w+")
        broker = subprocess.Popen([binary, "-c", str(config_path)], stdout=log, stderr=log)
        connections, transports = [], []
        errors = []
        async def error_cb(error):
            errors.append(type(error).__name__)
        try:
            for _ in range(100):
                if broker.poll() is not None:
                    log.seek(0)
                    raise RuntimeError("Broker startup failed: " + log.read())
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", number)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(.02)
            for host in (cpu, gpu):
                connections.append(await nats.connect(
                    f"nats://127.0.0.1:{number}", user=host, password=passwords[host],
                    inbox_prefix=f"_INBOX.{host}".encode(), allow_reconnect=False,
                    error_cb=error_cb, connect_timeout=2))
            seen, held, release = [], asyncio.Event(), asyncio.Event()
            async def echo(request):
                seen.append(request)
                return {"host": request.target_host_id, "source": request.source_host_id,
                        "value": request.payload}
            async def slow(request):
                seen.append(request)
                held.set()
                await release.wait()
                return {"finished": True}
            async def control(request):
                return {"host": request.target_host_id, "alive": True}
            a = HostBackplane(connections[0], authority_id=authority, host_id=cpu,
                              handlers={"echo": echo}, authorized_sources={gpu})
            b = HostBackplane(connections[1], authority_id=authority, host_id=gpu,
                              handlers={"echo": echo, "slow": slow, "control": control},
                              authorized_sources={cpu}, max_normal=1,
                              control_operations=frozenset({"control"}))
            transports = [a, b]
            application_check = configure(a, b) if configure else None
            await asyncio.gather(a.start(), b.start())
            start = time.monotonic()
            result = await a.request(gpu, "echo", {"canary": "cpu-to-gpu"}, request_id=str(uuid4()))
            assert result["host"] == gpu and result["source"] == cpu
            reverse = await b.request(cpu, "echo", {"canary": "gpu-to-cpu"}, request_id=str(uuid4()))
            assert reverse["host"] == cpu and reverse["source"] == gpu
            routing_ms = (time.monotonic() - start) * 1000
            # A timed-out request is executed once, remains running, and is not
            # automatically resubmitted. Control remains usable during that work.
            request_id = str(uuid4())
            pending = asyncio.create_task(a.request(gpu, "slow", {}, request_id=request_id, timeout=.1))
            await asyncio.wait_for(held.wait(), 2)
            control_start = time.monotonic()
            assert (await a.request(gpu, "control", {}, request_id=str(uuid4()), lane="control"))["alive"]
            control_ms = (time.monotonic() - control_start) * 1000
            try:
                await a.request(gpu, "echo", {}, request_id=str(uuid4()))
                raise AssertionError("Busy lane should refuse, not queue unbounded work")
            except BackplaneError as error:
                assert error.code == "HOST_BUSY"
            try:
                await pending
                raise AssertionError("Timeout expected")
            except NATSTimeout:
                pass
            assert sum(request.request_id == request_id for request in seen) == 1
            release.set()
            await asyncio.gather(*list(b.running["normal"]))
            # Each source may publish only under its own source identity. An
            # attempted forged request is rejected by the real NATS server.
            before = len(seen)
            await connections[0].publish(f"{prefix}.rpc.{gpu}.{cpu}.normal", encode({
                "version": 1, "requestId": str(uuid4()), "operation": "echo", "payload": {}}))
            await connections[0].flush()
            await asyncio.sleep(.05)
            assert len(seen) == before and errors
            # Unknown operation cannot become arbitrary runtime or shell access.
            try:
                await a.request(gpu, "arbitrary_shell", {}, request_id=str(uuid4()))
                raise AssertionError("Unregistered operation accepted")
            except BackplaneError as error:
                assert error.code == "OPERATION_UNAVAILABLE"
            try:
                await a.request(gpu, "slow", {}, request_id=str(uuid4()), lane="control")
                raise AssertionError("Normal work must not take reserved control capacity")
            except BackplaneError as error:
                assert error.code == "CONTROL_OPERATION_REQUIRED"
            application_result = await application_check() if application_check else None
            return {"application": application_result, "realBroker": True, "bidirectionalRouting": True,
                    "sourceForgeryDenied": True, "oneAttemptAfterTimeout": True,
                    "boundedBusyAdmission": True, "reservedControlLane": True,
                    "unregisteredOperationDenied": True,
                    "twoRequestsRoundtripMs": round(routing_ms, 3),
                    "controlDuringHeldWorkMs": round(control_ms, 3),
                    "scope": "two logical hosts on CPU loopback; not physical two-host or native-agent proof"}
        finally:
            release.set() if "release" in locals() else None
            for transport in transports:
                await transport.close()
            for connection in connections:
                await connection.close()
            broker.terminate()
            try:
                broker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                broker.kill()
                broker.wait()
            log.close()


if __name__ == "__main__":
    import sys
    print(json.dumps(asyncio.run(qualify(sys.argv[1])), indent=2))
