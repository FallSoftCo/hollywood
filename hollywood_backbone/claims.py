"""One transaction for native work claims, shared by both LAX runtime layouts."""
from . import admission
from .store import Conflict, Denied, json_value


def claim(store, c, a, operation, host, capabilities, *, expected_delivery=None):
    if a["kind"] != "agent":
        raise Denied("Only an execution connector may claim work")
    c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(a["id"]),))
    placement = c.execute(
        "SELECT * FROM hb_placement WHERE agent=%s AND host=%s AND enabled FOR SHARE",
        (a["id"], host),
    ).fetchone()
    if not placement:
        raise Denied("Connector host not enrolled for this agent")
    store.native_role_capability(c, a, capabilities)
    if not admission.CAPABILITIES <= set(capabilities):
        raise Conflict("Connector upgrade required for host admission")
    previous = c.execute(
        "SELECT * FROM hb_claim WHERE agent=%s AND operation=%s", (a["id"], operation)
    ).fetchone()
    if previous:
        if str(previous["host"]) != host:
            raise Conflict("Claim host changed")
        if previous["delivery"]:
            row = c.execute(
                "SELECT e.room FROM hb_delivery d JOIN hb_event e ON e.id=d.event WHERE d.id=%s",
                (previous["delivery"],),
            ).fetchone()
            store.room(c, a, row["room"])
        if expected_delivery is not None and str(previous["delivery"]) != expected_delivery:
            raise Conflict("Claim belongs to another original work")
        return store.claim_receipt(c, previous["delivery"])
    admission.presence(store, c, a, host, capabilities)
    admission.lock(c, host)
    from . import purpose_claims
    purpose=expected_delivery is not None and purpose_claims.enabled(c,a,host)
    observed = (purpose_claims.diagnose(store,c,a,host,expected_delivery) if purpose else
                admission.diagnose(store,c,a,host,capabilities,record=False))
    if (expected_delivery is not None and observed["eligibleDeliveryId"] is not None
            and str(observed["eligibleDeliveryId"]) != expected_delivery):
        observed = {**observed, "eligibleDeliveryId": None, "reason": "another_work_ahead"}
    from .work import claim_candidate

    row = (
        claim_candidate(store, c, a, observed["eligibleDeliveryId"])
        if observed["eligibleDeliveryId"]
        else None
    )
    identity = row["id"] if row else None
    # Lock the selected delivery before its trace, matching cancellation
    # triggers. Observations never lock a delivery after updating a trace.
    admission.record_observation(c, host, observed)
    if row:
        from .cooperation import verify_admission
        from .memory import verify_admission as verify_briefing

        event = store.readable_event(c, a, row["event"])
        verify_briefing(c, event, capabilities)
        verify_admission(store, c, a, event, capabilities)
        store.room(c, a, event["room"])
        admission.reserve(c, host, identity, observed["queueClass"])
        c.execute(
            "UPDATE hb_delivery SET state='claimed',host=%s,claim_id=%s,generation=generation+1,revision=revision+1,updated_at=clock_timestamp() WHERE id=%s",
            (host, operation, identity),
        )
    # A confirmed deferral is an observation, not an accepted operation.
    # Keep the exact purpose claim ID available when capacity becomes ready.
    if expected_delivery is not None and identity is None:
        return {"delivery": None, "admission": json_value(observed)}
    c.execute(
        "INSERT INTO hb_claim VALUES(%s,%s,%s,%s)", (a["id"], operation, host, identity)
    )
    receipt = store.claim_receipt(c, identity)
    receipt["admission"] = json_value(observed)
    return receipt
