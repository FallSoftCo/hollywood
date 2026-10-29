"""Host-addressed NATS transport, independent of Hollywood execution authority.

Core request/reply deliberately does not retry commands. Durable acceptance,
reconciliation and original operation IDs remain in the owning application.
Broker permissions MUST restrict each host to its own request/event source
prefix; source identity comes from that authorized subject, never JSON fields.
An explicit peer set can further narrow admission. None means broker-enrolled
peers, not anonymous access; domain actor authorization remains mandatory.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID


class BackplaneError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def identity(value: str) -> str:
    result = str(UUID(value))
    if result != value:
        raise ValueError("Canonical UUID required")
    return result


def encode(value: dict) -> bytes:
    body = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > 512 * 1024:
        raise BackplaneError("PAYLOAD_TOO_LARGE")
    return body


def decode(value: bytes) -> dict:
    if len(value) > 512 * 1024:
        raise BackplaneError("PAYLOAD_TOO_LARGE")
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise BackplaneError("INVALID_ENVELOPE")
            result[key] = item
        return result
    result = json.loads(value, object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(result, dict):
        raise BackplaneError("INVALID_ENVELOPE")
    return result


@dataclass(frozen=True)
class HostRequest:
    authority_id: str
    source_host_id: str
    target_host_id: str
    request_id: str
    operation: str
    payload: dict
    lane: str


Handler = Callable[[HostRequest], Awaitable[dict]]


class HostBackplane:
    def __init__(self, connection, *, authority_id: str, host_id: str,
                 handlers: dict[str, Handler], authorized_sources: set[str] | None,
                 max_normal: int = 16, max_control: int = 2,
                 control_operations: frozenset[str] = frozenset()):
        if max_normal < 1 or max_control < 1:
            raise ValueError("Positive concurrency limits required")
        self.connection = connection
        self.authority_id = identity(authority_id)
        self.host_id = identity(host_id)
        self.handlers = dict(handlers)
        self.control_operations = control_operations
        if not control_operations.issubset(handlers):
            raise ValueError("Control handlers must be registered")
        self.authorized_sources = (None if authorized_sources is None
                                   else {identity(x) for x in authorized_sources})
        self.limits = {"normal": max_normal, "control": max_control}
        self.running = {"normal": set(), "control": set()}
        self.subscription = None
        self.closing = False

    @property
    def prefix(self):
        return f"hollywood.v1.{self.authority_id}"

    async def start(self):
        if self.closing:
            raise BackplaneError("HOST_DRAINING")
        if self.subscription is not None:
            return
        self.subscription = await self.connection.subscribe(
            f"{self.prefix}.rpc.*.{self.host_id}.*", cb=self._receive,
            pending_msgs_limit=64, pending_bytes_limit=2 * 1024 * 1024)
        await self.connection.flush()

    async def _receive(self, message):
        # NATS dispatch callbacks remain short, so a slow handler never stalls
        # receipt of cancellation/control or another agent's independent work.
        try:
            parts = message.subject.split(".")
            if (len(parts) != 7 or ".".join(parts[:3]) != self.prefix
                    or parts[3] != "rpc" or parts[5] != self.host_id):
                raise BackplaneError("INVALID_ROUTE")
            source = identity(parts[4])
            lane = parts[6]
            if self.authorized_sources is not None and source not in self.authorized_sources:
                raise BackplaneError("SOURCE_NOT_ENROLLED")
            if lane not in self.running:
                raise BackplaneError("INVALID_LANE")
            value = decode(message.data)
            if (set(value) != {"version", "requestId", "operation", "payload"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or not isinstance(value["payload"], dict)
                    or not isinstance(value["operation"], str)):
                raise BackplaneError("INVALID_ENVELOPE")
            request_id = identity(value["requestId"])
            operation = value["operation"]
            if operation not in self.handlers:
                raise BackplaneError("OPERATION_UNAVAILABLE")
            if lane == "control" and operation not in self.control_operations:
                raise BackplaneError("CONTROL_OPERATION_REQUIRED")
            if self.closing:
                raise BackplaneError("HOST_DRAINING")
            if len(self.running[lane]) >= self.limits[lane]:
                raise BackplaneError("HOST_BUSY")
            request = HostRequest(self.authority_id, source, self.host_id,
                                  request_id, operation, value["payload"], lane)
            task = asyncio.create_task(self._execute(message, request))
            self.running[lane].add(task)
            task.add_done_callback(self.running[lane].discard)
        except (ValueError, KeyError, BackplaneError, TypeError) as error:
            code = error.code if isinstance(error, BackplaneError) else "INVALID_ENVELOPE"
            if message.reply:
                await message.respond(encode({"version": 1, "ok": False, "error": code}))

    async def _execute(self, message, request):
        try:
            result = await self.handlers[request.operation](request)
            response = {"version": 1, "ok": True, "requestId": request.request_id,
                        "hostId": self.host_id, "result": result}
            body = encode(response)
        except BackplaneError as error:
            body = encode({"version": 1, "ok": False, "requestId": request.request_id,
                           "hostId": self.host_id, "error": error.code})
        except Exception:
            # Do not leak credentials, raw tool output or internal exception text.
            body = encode({"version": 1, "ok": False, "requestId": request.request_id,
                           "hostId": self.host_id, "error": "HANDLER_FAILED"})
        if message.reply:
            try:
                await message.respond(body)
            except Exception:
                # Lost reply is unknown delivery, not permission to execute again.
                pass

    async def request(self, target_host_id: str, operation: str, payload: dict,
                      *, request_id: str, lane: str = "normal", timeout: float = 10):
        target_host_id = identity(target_host_id)
        request_id = identity(request_id)
        if lane not in self.running or not 0 < timeout <= 60:
            raise ValueError("Invalid lane or timeout")
        if self.closing:
            raise BackplaneError("HOST_DRAINING")
        if not self.connection.is_connected:
            raise BackplaneError("HOST_UNAVAILABLE")
        body = encode({"version": 1, "requestId": request_id,
                       "operation": operation, "payload": payload})
        # One attempt only. The caller retains this exact request identity and
        # reconciles its owning domain receipt if this await times out.
        message = await self.connection.request(
            f"{self.prefix}.rpc.{self.host_id}.{target_host_id}.{lane}",
            body, timeout=timeout)
        result = decode(message.data)
        if result.get("version") != 1 or type(result.get("ok")) is not bool:
            raise BackplaneError("INVALID_RESPONSE")
        if not result["ok"]:
            raise BackplaneError(result.get("error", "REMOTE_FAILED"))
        if result.get("requestId") != request_id or result.get("hostId") != target_host_id:
            raise BackplaneError("RESPONSE_IDENTITY_MISMATCH")
        return result["result"]

    async def close(self):
        self.closing = True
        if self.subscription is not None:
            from nats.errors import ConnectionClosedError
            try:
                await self.subscription.unsubscribe()
            except ConnectionClosedError:
                # The subscription is already gone. Still drain the handlers
                # this adapter admitted before transport loss.
                pass
            finally:
                self.subscription = None
        tasks = [task for lane in self.running.values() for task in lane]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # This adapter does not own/close a shared NATS or Codex connection.


async def connect_host(*, server: str, host_id: str, password: str,
                       ca_file: str, timeout: float = 8, persistent: bool = False, closed_cb=None,
                       disconnected_cb=None, reconnected_cb=None):
    """Create an authenticated TLS connection with a bounded initial handshake.

    A connection supervisor may reconnect later. This helper and the request
    adapter never replay operations after a connection failure.
    """
    import ssl
    from urllib.parse import urlsplit
    from nats.aio.client import Client

    host_id = identity(host_id)
    servers = [server] if isinstance(server, str) else list(server)
    if not 1 <= len(servers) <= 16 or len(set(servers)) != len(servers):
        raise ValueError("Explicit unique NATS seed endpoints required")
    for endpoint in servers:
        address = urlsplit(endpoint)
        if (address.scheme != "tls" or not address.hostname or address.username
                or address.password or address.path not in {"", "/"}
                or address.query or address.fragment or not 0 < timeout <= 15
                or not isinstance(password, str) or len(password) < 32):
            raise ValueError("TLS host endpoint and private host credential required")
    context = ssl.create_default_context(cafile=ca_file)
    connection = Client()
    try:
        await asyncio.wait_for(connection.connect(
            servers, user=host_id, password=password, tls=context,
            inbox_prefix=f"_INBOX.{host_id}".encode(),
            allow_reconnect=persistent or disconnected_cb is not None, pending_size=0, reconnect_time_wait=2,
            max_reconnect_attempts=-1 if persistent else 1, connect_timeout=min(timeout, 3),
            ping_interval=10, max_outstanding_pings=2,
            error_cb=_quiet_connection_error, closed_cb=closed_cb,
            disconnected_cb=disconnected_cb, reconnected_cb=reconnected_cb), timeout=timeout)
        return connection
    except BaseException:
        try:
            await asyncio.wait_for(connection.close(), timeout=2)
        except Exception:
            pass
        raise


async def _quiet_connection_error(error):
    # Caller receives the typed failure. Never log credential-bearing endpoints
    # or start a model turn from a transport error callback.
    pass
