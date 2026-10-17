"""Actual private PostgreSQL/authenticated HTTP; native lifecycle is synthetic.

Captured failure shape: a native agent delegates while replying to an agent
result. The result is conversational context, never an executable parent.
"""
import asyncio
import copy
import json

from psycopg.types.json import Jsonb

from report_scope_fixture import Fixture, uid


async def main():
    f = Fixture()
    await f.start()
    a, b = f.agents["worker"], f.agents["peer"]
    checks = []
    try:
        # Synthetic fixture has a private execution parent and a public result
        # being discussed. The route must return no private parent text.
        f.sql("UPDATE hb_event SET audience='direct',body='SYNTHETIC PRIVATE PARENT BODY' WHERE id=%s",
              (a["source"]["requestId"],))
        original_result = f.sql("SELECT result_event FROM hb_delivery WHERE id=%s", (a["prior"],))["result_event"]
        path = "rooms/" + f.ids["A"] + "/messages"
        body = {"operationId": uid(), "kind": "request", "audience": "room",
                "recipientId": b["id"], "sourceEventId": str(original_result),
                "expectsReply": True, "body": "SYNTHETIC review of original result"}
        source = f.source(a, "hollywood_request")
        accepted = await f.api(a, "POST", path, body, source)
        child = accepted["deliveries"][0]["id"]
        child_event = accepted["event"]["id"]
        route = "deliveries/" + child + "/delegation"
        expected_parent = {key: source[key] for key in
            ("authorityId", "agentId", "hostId", "deliveryId", "generation", "claimId",
             "requestId", "contextId", "roleRevision")}
        first = await f.api(a, "GET", route)
        assert "SYNTHETIC PRIVATE PARENT BODY" not in json.dumps(first)
        assert first["schema"] == "hollywood-native-delegation/v1"
        assert first["childRequestId"] == child_event and first["childDeliveryId"] == child
        assert first["conversationSourceEventId"] == str(original_result)
        assert first["parent"] == expected_parent
        assert first["parent"]["requestId"] != str(original_result)
        assert first["nativeCallId"] == source["callId"]
        assert first["proof"]["recordedBy"] == "native_acceptance"
        checks.append("Real HTTP native acceptance commits parent independent of result-source conversation")

        # A later retry from another genuine (fixture-only) native parent returns
        # the accepted command. It cannot replace the original delegation.
        next_request = f.store.post(f.owner, f.ids["B"], uid(),
            {"kind": "request", "audience": "room", "recipientId": a["id"],
             "body": "SYNTHETIC later execution, not the delegation parent"})
        f.bind(a, next_request["deliveries"][0]["id"], next_request["event"]["id"])
        retry_source = f.source(a, "hollywood_request")
        retried = await f.api(a, "POST", path, body, retry_source)
        assert retried["event"]["id"] == child_event
        assert await f.api(a, "GET", route) == first
        assert f.sql("SELECT count(*) AS n FROM hb_delivery WHERE event=%s", (child_event,))["n"] == 1
        checks.append("Later accepted-command retry leaves original parent and child unchanged")

        # Historical execution no longer needs a live executor/current role.
        f.sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (source["deliveryId"],))
        f.sql("UPDATE hb_executor SET state='stopped' WHERE delivery=%s", (source["deliveryId"],))
        assert await f.api(a, "GET", route) == first
        await f.api(b, "GET", route, status=403)
        assert await f.api("owner", "GET", route) == first
        await f.api(a, "GET", route + "?parentDeliveryId=" + uid(), status=400)
        f.sql("UPDATE hb_membership SET can_read=false WHERE room=%s AND principal=%s", (f.ids["B"], a["id"]))
        await f.api(a, "GET", route, status=403)
        f.sql("UPDATE hb_membership SET can_read=true WHERE room=%s AND principal=%s", (f.ids["B"], a["id"]))
        checks.append("Read authorization covers both parent and child; recipient cannot learn parent's private execution; historical terminal parent remains inspectable")

        # The migration recovers the still-provable original SQL transaction,
        # never the later retry (its audit row has a different transaction ID).
        f.sql("DELETE FROM hb_native_delegation WHERE child_delivery=%s", (child,))
        await f.api(a, "GET", route, status=409)
        f.sql("DELETE FROM hb_schema WHERE version=30")
        f.store.migrate()
        recovered = await f.api(a, "GET", route)
        assert recovered["parent"] == first["parent"] and recovered["nativeCallId"] == first["nativeCallId"]
        assert recovered["proof"]["recordedBy"] == "verified_original_transaction"
        before = copy.deepcopy(recovered)
        f.store.migrate()
        assert await f.api(a, "GET", route) == before
        checks.append("One-time legacy recovery requires original command/event/effect transaction; later retry cannot retarget; schema rerun is stable")

        # Missing or changed evidence fails closed. No request/result is emitted.
        count = f.sql("SELECT count(*) AS n FROM hb_delivery")["n"]
        old_binding = f.sql("SELECT * FROM hb_agent_execution WHERE delivery=%s AND generation=1", (source["deliveryId"],))
        f.sql("UPDATE hb_agent_execution SET context_id='SYNTHETIC-altered-context' WHERE delivery=%s", (source["deliveryId"],))
        await f.api(a, "GET", route, status=409)
        f.sql("UPDATE hb_agent_execution SET context_id=%s WHERE delivery=%s", (old_binding["context_id"], source["deliveryId"]))
        old_command = f.sql("SELECT wire_request FROM hb_command WHERE event=%s", (child_event,))["wire_request"]
        f.sql("UPDATE hb_command SET wire_request=%s WHERE event=%s", (Jsonb({"SYNTHETIC":"altered"}), child_event))
        await f.api(a, "GET", route, status=409)
        f.sql("UPDATE hb_command SET wire_request=%s WHERE event=%s", (Jsonb(old_command), child_event))
        assert f.sql("SELECT count(*) AS n FROM hb_delivery")["n"] == count
        checks.append("Changed canonical wire or native binding holds; read never admits or repeats execution")
        f.sql("DELETE FROM hb_native_delegation WHERE child_delivery=%s", (child,))
        f.sql("DELETE FROM hb_schema WHERE version=30")
        f.store.migrate()
        await f.api(a, "GET", route, status=409)
        assert f.sql("SELECT count(*) AS n FROM hb_delivery")["n"] == count
        checks.append("Legacy rows whose original transaction proof was rewritten remain unavailable; no timestamp or latest-retry inference")

        result = {"state":"passed", "actualPostgreSQL":True, "actualAuthenticatedHTTP":True,
                  "nativeLifecycle":"synthetic", "modelsStarted":0, "productionChanged":False,
                  "checks":checks, "httpCalls":len(f.trace)}
        (f.out / "native-delegation-passed.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        await f.close()


if __name__ == "__main__":
    asyncio.run(main())
