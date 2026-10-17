"""Real PG/HTTP recovery of synthetic pre-native failure; no models or workers."""
import asyncio
import copy
import json

from report_scope_fixture import Fixture, uid


async def main():
    f = Fixture()
    await f.start()
    a = f.agents["worker"]
    checks = []
    try:
        def failed(state="failed"):
            out = f.store.post(f.owner, f.ids["A"], uid(), {
                "kind":"request", "audience":"room", "recipientId":a["id"],
                "body":"SYNTHETIC native-admission failure"})
            identity = out["deliveries"][0]["id"]
            f.sql("UPDATE hb_delivery SET state='claimed',generation=1,claim_id='SYNTHETIC-no-executor' WHERE id=%s", (identity,))
            result = f.store.transition(a["id"], identity, uid(), {
                "generation":1, "claimId":"SYNTHETIC-no-executor", "state":"failed",
                "body":"SYNTHETIC scope refusal before any native admission"})
            if state != "failed":
                f.sql("UPDATE hb_delivery SET state=%s WHERE id=%s", (state, identity))
            return identity, result
        identity, original = failed()
        before = copy.deepcopy(f.sql("SELECT * FROM hb_delivery WHERE id=%s", (identity,)))
        result_before = copy.deepcopy(f.sql("SELECT * FROM hb_event WHERE id=%s", (before["result_event"],)))
        body = {"schema":"hollywood-pre-native-recovery/v1", "operationId":uid(),
                "revision":before["revision"], "generation":1,
                "resultEventId":str(before["result_event"]), "note":"SYNTHETIC reviewed no-admission proof"}
        route = "deliveries/" + identity + "/recover-before-native"
        await f.api(a, "POST", route, body, status=403)
        stale = {**body,"revision":before["revision"]+1}
        await f.api("owner", "POST", route, stale, status=409)
        accepted = await f.api("owner", "POST", route, body)
        assert accepted["schema"] == body["schema"] and accepted["state"] == "pending"
        assert accepted["originalAttemptPreserved"] and not accepted["nativeExecutionStarted"]
        assert await f.api("owner", "POST", route, body) == accepted
        assert f.sql("SELECT count(*) AS n FROM hb_delivery_recovery WHERE delivery=%s", (identity,))["n"] == 1
        assert f.sql("SELECT before_snapshot FROM hb_delivery_recovery WHERE delivery=%s", (identity,))["before_snapshot"] == __import__("hollywood_backbone.store",fromlist=["json_value"]).json_value(before)
        assert f.sql("SELECT * FROM hb_event WHERE id=%s", (before["result_event"],)) == result_before
        assert f.sql("SELECT count(*) AS n FROM hb_transition WHERE delivery=%s", (identity,))["n"] == 1
        changed = {**body,"note":"SYNTHETIC changed original operation"}
        await f.api("owner", "POST", route, changed, status=409)
        checks.append("Explicit administrator CAS recovery preserves original failed event/transition/snapshot; duplicate returns one original acceptance")

        for state in ("unknown", "cancelled"):
            other, out = failed(state)
            row = f.sql("SELECT * FROM hb_delivery WHERE id=%s", (other,))
            request = {**body,"operationId":uid(),"revision":row["revision"],"resultEventId":str(row["result_event"])}
            await f.api("owner", "POST", "deliveries/"+other+"/recover-before-native", request, status=409)
            assert f.sql("SELECT state FROM hb_delivery WHERE id=%s", (other,))["state"] == state
        other, out = failed()
        f.sql("UPDATE hb_delivery SET native_thread='SYNTHETIC-possibly-started' WHERE id=%s", (other,))
        row = f.sql("SELECT * FROM hb_delivery WHERE id=%s", (other,))
        await f.api("owner", "POST", "deliveries/"+other+"/recover-before-native",
            {**body,"operationId":uid(),"revision":row["revision"],"resultEventId":str(row["result_event"])}, status=409)
        checks.append("Unknown/cancelled/native-pointer attempts cannot use pre-native recovery")

        # A settled native attempt has an immutable binding/executor/effect.
        old = f.sql("SELECT * FROM hb_delivery WHERE id=%s", (a["prior"],))
        f.sql("UPDATE hb_delivery SET state='failed',native_thread=NULL,native_turn=NULL WHERE id=%s", (a["prior"],))
        await f.api("owner", "POST", "deliveries/"+a["prior"]+"/recover-before-native",
            {**body,"operationId":uid(),"revision":old["revision"],"resultEventId":str(old["result_event"])}, status=409)
        checks.append("Stopped prior execution metadata still prohibits replay, even if mutable native pointers are empty")
        result = {"state":"passed","checks":checks,"httpCalls":len(f.trace),
                  "actualPostgreSQL":True,"actualAuthenticatedHTTP":True,
                  "nativeLifecycle":"synthetic","modelsStarted":0,"productionChanged":False}
        (f.out/"pre-native-recovery-passed.json").write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    finally:
        await f.close()


if __name__=="__main__":
    asyncio.run(main())
