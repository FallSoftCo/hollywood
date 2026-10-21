"""Writer work references use native claims; interpretation alone reserves nothing."""
from .store import Conflict, Denied
from .claims import claim


def current(store, c, actor, binding, p):
    if binding["active_turn"] is not None:
        raise Conflict("Original writer turn is unsettled")
    system = c.execute("SELECT * FROM hb_context_system WHERE agent=%s AND enabled",
                       (actor["id"],)).fetchone()
    work = c.execute("""SELECT * FROM hb_context_material
        WHERE id=%s AND agent=%s AND kind='work'""",
        (p["value"]["workId"], actor["id"])).fetchone()
    if not system or not work:
        raise Denied("Own accepted work required")
    delivery = c.execute("SELECT * FROM hb_delivery WHERE event=%s AND agent=%s",
                         (work["source"], actor["id"])).fetchone()
    if (work["state"] not in ("pending", "running") or not delivery
            or delivery["cancellation_requested"]):
        raise Conflict("Original work no longer admits execution")
    event = store.readable_event(c, actor, work["source"])
    from .context_role_work import validate
    validate(c, store, actor, event)
    store.room(c, actor, event["room"], "can_post")
    # Continuation observes the same native claim; it never creates a replacement
    # ownership generation for a subsequent writer turn.
    if delivery["state"] == "unknown":
        from .purpose_terminal_recovery import reconcile
        delivery = reconcile(store, c, delivery["id"])
    prior = c.execute("SELECT * FROM hb_claim WHERE agent=%s AND delivery=%s",
                      (actor["id"], delivery["id"])).fetchone()
    if prior and (delivery["state"] not in ("claimed", "running")
            or delivery["host"] != system["host"]
            or delivery["claim_id"] != prior["operation"]):
        raise Conflict("Original native work owner changed")
    return system, delivery, prior


def ready(store, c, actor, binding, p):
    if set(p["value"]) != {"workId"}:
        raise ValueError("Exact original work required")
    _, delivery, prior = current(store, c, actor, binding, p)
    if not prior:
        raise Conflict("Original native admission receipt unavailable")
    return store.claim_receipt(c, delivery["id"])


def admit(store, c, actor, binding, p):
    if set(p["value"]) != {"workId", "capabilities"}:
        raise ValueError("Exact original work and native capabilities required")
    system, delivery, prior = current(store, c, actor, binding, p)
    if prior:
        return store.claim_receipt(c, delivery["id"])
    if delivery["state"] != "pending":
        raise Conflict("Unadmitted legacy context work requires explicit recovery")
    return claim(store, c, actor, p["operationId"], str(system["host"]),
                 p["value"]["capabilities"], expected_delivery=str(delivery["id"]))
