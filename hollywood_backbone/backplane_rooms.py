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
        except (ValueError, TypeError):
            raise BackplaneError("INVALID_PARAMETERS") from None

    def handlers(self):
        return {"hollywood.rooms.list": self.catalog}


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
