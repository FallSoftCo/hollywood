"""Immutable native activity in the originating room; never an execution request."""

from .store import Conflict, Denied, digest, json_value


def action_identity(delivery, generation, turn, item):
    """One action across lifecycle phases; delivery/turn scope prevents collisions."""
    return "action-" + digest([str(delivery), generation, str(turn), item])


def project(event):
    """Add read metadata to old records without rewriting their original receipts."""
    if event["data"].get("type") != "executionActivity":
        return event
    data = event["data"]
    return {
        **event,
        "data": {
            **data,
            "activityVersion": 1,
            "actionId": action_identity(
                data["deliveryId"], data["generation"], data["turn"], data["itemId"]
            ),
        },
    }


def append(store, actor_id, identity, payload):
    with store.pool.connection() as c:
        a = store.actor(c, actor_id)
        d = c.execute(
            "SELECT * FROM hb_delivery WHERE id=%s AND authority=%s AND agent=%s FOR UPDATE",
            (identity, a["authority"], a["id"]),
        ).fetchone()
        if not d:
            raise Denied("Native activity owner unavailable")
        if d["generation"] != payload["generation"] or d["claim_id"] != payload["claimId"]:
            raise Conflict("Stale native activity owner")
        request = c.execute("SELECT * FROM hb_event WHERE id=%s", (d["event"],)).fetchone()
        store.room(c, a, request["room"], "can_post")
        pointer = (payload["nativeThread"], payload["nativeTurn"])
        if any(
            known and str(known) != supplied
            for known, supplied in zip((d["native_thread"], d["native_turn"]), pointer)
        ):
            raise Conflict("Native activity belongs to another execution")
        key = (
            identity,
            d["generation"],
            payload["nativeTurn"],
            payload["itemId"],
            payload["phase"],
        )
        previous = c.execute(
            "SELECT * FROM hb_activity WHERE delivery=%s AND generation=%s AND native_turn=%s AND item_id=%s AND phase=%s",
            key,
        ).fetchone()
        fingerprint = digest(payload)
        if previous:
            if previous["digest"] != fingerprint:
                raise Conflict("Native activity identity changed contents")
            return store.receipt(c, previous["event"])
        if d["state"] not in (
            "claimed",
            "running",
            "cancel_requested",
            "unknown",
            "completed",
            "failed",
            "cancelled",
        ):
            raise Conflict("Unadmitted delivery cannot publish native activity")
        if d["state"] in ("completed", "failed", "cancelled") and not all(
            (d["native_thread"], d["native_turn"])
        ):
            raise Conflict("Terminal delivery has no native owner")
        # Late log recovery is permitted for this original fenced owner; it cannot
        # start work, change the terminal state or create a new delivery.
        c.execute(
            "UPDATE hb_delivery SET native_thread=COALESCE(native_thread,%s),native_turn=COALESCE(native_turn,%s) WHERE id=%s",
            (*pointer, identity),
        )
        item = payload["item"]
        binding = c.execute(
            "SELECT * FROM hb_native_goal_binding WHERE delivery=%s AND generation=%s",
            (identity, d["generation"]),
        ).fetchone()
        if item.get("nativeGoal") and (
            not binding or item["nativeGoal"]["goalId"] != binding["native_goal_id"]
        ):
            raise Conflict("Native goal progress differs from captured binding")
        audience = request["audience"]
        recipient = request["author"] if audience == "direct" else None
        if item["nativeType"] == "dynamicToolCall" and item["title"] == "hollywood_ask":
            from .attention import human_owner

            audience, recipient = "direct", human_owner(c, request)
        event = store.event(
            c,
            a,
            request["room"],
            "progress",
            item["title"],
            source=request["id"],
            audience=audience,
            recipient=recipient,
            data={
                "type": "executionActivity",
                "activityVersion": 1,
                "actionId": action_identity(identity, d["generation"], pointer[1], payload["itemId"]),
                "deliveryId": identity,
                "agentId": str(a["id"]),
                "hostId": str(d["host"]),
                "generation": d["generation"],
                "thread": pointer[0],
                "turn": pointer[1],
                "itemId": payload["itemId"],
                "phase": payload["phase"],
                "nativeItemDigest": payload["nativeItemDigest"],
                "goalId": str(binding["goal"])
                if binding and binding["goal"]
                else request["data"].get("goalId"),
                "workId": str(binding["work"])
                if binding and binding["work"]
                else request["data"].get("workId"),
                **item,
            },
        )
        c.execute(
            "INSERT INTO hb_activity VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                identity,
                d["generation"],
                *pointer,
                payload["itemId"],
                payload["phase"],
                fingerprint,
                event,
            ),
        )
        result = store.receipt(c, event)
        assert result["deliveries"] == []
        return json_value(result)
