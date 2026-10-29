"""Canonical room reads for authenticated, enrolled single-context agents."""
import asyncio
from uuid import UUID

from . import native_effects
from .backplane import BackplaneError
from .store import Denied


class AgentRoomReads:
    def __init__(self, bindings):
        self.bindings = bindings

    async def catalog(self, request):
        if set(request.payload) != {"actorId"}:
            raise BackplaneError("INVALID_PARAMETERS")
        try:
            actor = str(UUID(request.payload["actorId"]))
            binding = await asyncio.to_thread(
                self.bindings.resolve, authority_id=request.authority_id,
                authenticated_host_id=request.source_host_id, principal_id=actor)
            # Reuse the canonical domain read and its permissions. Never return
            # credential digests or let a supplied actor ID act as authentication.
            with native_effects.transport(actor, binding.token_digest,
                                          "GET", "catalog", "rooms", None, None, admission=binding):
                return await asyncio.to_thread(self.bindings.store.catalog, actor)
        except Denied:
            raise BackplaneError("AGENT_ACCESS_DENIED") from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    async def _read(self, request, method, path, args):
        actor = str(UUID(request.payload["actorId"]))
        binding = await asyncio.to_thread(self.bindings.resolve,
            authority_id=request.authority_id, authenticated_host_id=request.source_host_id,
            principal_id=actor)
        with native_effects.transport(actor, binding.token_digest, "GET", method,
                                      path, None, None, admission=binding):
            return await asyncio.to_thread(getattr(self.bindings.store, method), actor, *args)

    async def agents(self, request):
        try:
            if set(request.payload) - {"actorId", "after", "limit"}:
                raise ValueError("Unknown directory parameters")
            after = request.payload.get("after")
            after = str(UUID(after)) if after is not None else None
            limit = request.payload.get("limit", 50)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError("Invalid directory limit")
            return await self._read(request, "agent_directory", "agents", (after, limit))
        except Denied:
            raise BackplaneError("AGENT_ACCESS_DENIED") from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    async def history(self, request):
        try:
            p = request.payload
            if set(p) - {"actorId", "roomId", "after", "before", "limit", "view"}:
                raise ValueError("Unknown history parameters")
            room = str(UUID(p["roomId"]))
            after, before, limit, view = p.get("after", 0), p.get("before"), p.get("limit", 100), p.get("view", "all")
            if (type(after) is not int or not 0 <= after <= 9007199254740991
                    or before is not None and (type(before) is not int or not 1 <= before <= 9007199254740991)
                    or "after" in p and "before" in p
                    or type(limit) is not int or not 1 <= limit <= 100
                    or view not in {"all", "conversation", "activity"}):
                raise ValueError("Invalid history page")
            return await self._read(request, "history", f"rooms/{room}/history",
                                    (room, after, limit, before, view))
        except Denied:
            raise BackplaneError("AGENT_ACCESS_DENIED") from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    async def command(self, request):
        try:
            p = request.payload
            if set(p) != {"actorId", "operationId"} or not isinstance(p["operationId"], str) or not 1 <= len(p["operationId"]) <= 128:
                raise ValueError("Invalid original operation")
            return await self._read(request, "command", "commands/"+p["operationId"], (p["operationId"],))
        except Denied:
            raise BackplaneError("AGENT_ACCESS_DENIED") from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    def handlers(self):
        return {"hollywood.rooms.list": self.catalog, "hollywood.agents.list": self.agents,
                "hollywood.room.history": self.history, "hollywood.command.read": self.command}


class AgentRoomTransport(AgentRoomReads):
    """Peer communication using the same schema, receipts and effect boundary.

    This is not a human command ingress or an execution scheduler. Without the
    original registered native source, enrolled agents cannot acquire mutation
    authority merely by presenting a valid host transport credential.
    """
    async def send(self, request):
        from .message_input import Message
        from .store import Conflict
        if set(request.payload) - {"actorId", "roomId", "message", "nativeSource"}:
            raise BackplaneError("INVALID_PARAMETERS")
        try:
            actor = str(UUID(request.payload["actorId"]))
            room = str(UUID(request.payload["roomId"]))
            raw = request.payload["message"]
            message = Message.model_validate(raw)
            if not message.body.strip() or len(message.body.encode()) > 6000:
                raise ValueError("Message body is empty or too large")
            provenance = request.payload.get("nativeSource")
            if provenance is not None:
                import json
                provenance = native_effects.source(json.dumps(provenance))
                if (provenance["authorityId"] != request.authority_id
                        or provenance["agentId"] != actor
                        or provenance["hostId"] != request.source_host_id):
                    raise Denied("Native source belongs to another actor or host")
            binding = await asyncio.to_thread(
                self.bindings.resolve, authority_id=request.authority_id,
                authenticated_host_id=request.source_host_id, principal_id=actor)
            path = f"rooms/{room}/messages"
            payload = message.model_dump(mode="json", exclude={"operationId"}
                | ({"expectsReply"} if not message.expectsReply else set())
                | ({"evidenceReferences"} if message.evidenceReferences is None else set()))
            wire = {"method": "POST", "path": path, "body": raw}
            with native_effects.transport(actor, binding.token_digest,
                                          "POST", "post", path, raw, provenance, admission=binding):
                return await asyncio.to_thread(self.bindings.store.post,
                    actor, room, message.operationId, payload, wire=wire)
        except Denied:
            raise BackplaneError("AGENT_ACCESS_DENIED") from None
        except Conflict:
            raise BackplaneError("OPERATION_CONFLICT") from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    async def inbox(self, request):
        if set(request.payload) - {"actorId", "after", "limit"}:
            raise BackplaneError("INVALID_PARAMETERS")
        try:
            actor = str(UUID(request.payload["actorId"]))
            after, limit = request.payload.get("after"), request.payload.get("limit", 100)
            if ((after is not None and (type(after) is not int or not 0 <= after <= 9007199254740991))
                    or type(limit) is not int or not 1 <= limit <= 100):
                raise ValueError("Invalid inbox cursor")
            binding = await asyncio.to_thread(
                self.bindings.resolve, authority_id=request.authority_id,
                authenticated_host_id=request.source_host_id, principal_id=actor)
            with native_effects.transport(actor, binding.token_digest,
                                          "GET", "inbox", "inbox/pending", None, None, admission=binding):
                return await asyncio.to_thread(self.bindings.store.inbox, actor, after or 0, limit, True)
        except Denied:
            raise BackplaneError("AGENT_ACCESS_DENIED") from None
        except (ValueError, TypeError, KeyError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    def handlers(self):
        return {**super().handlers(), "hollywood.message.send": self.send,
                "hollywood.inbox.pending": self.inbox}
