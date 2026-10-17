
"""Real PG/HTTP Goal origin semantics; fixture native metadata is synthetic."""
import asyncio
import json

from edit_plan_fixture import Fixture, uid


async def main():
    f = Fixture()
    await f.start()
    owner = f.credentials["Owner"]["token"]
    global_id = f.credentials["Global"]["principalId"]
    author, reader = f.agents["i"], f.agents["outsider"]
    origin_room, target_room = uid(), uid()
    f.store.create_room(f.owner, origin_room, "Private planning origin", "Fixture only", global_id)
    f.store.create_room(f.owner, target_room, "Scoped engineering", "Fixture only", global_id)
    f.store.membership(f.owner, origin_room, author["id"],
        {"can_read": True, "can_post": True, "can_request": True}, 0)
    f.store.membership(f.owner, target_room, reader["id"],
        {"can_read": True, "can_post": True, "can_request": False}, 0)
    source = f.store.post(author["id"], origin_room, uid(),
        {"kind": "request", "audience": "room", "recipientId": global_id,
         "body": "PRIVATE_ORIGIN_BODY_IS_NOT_EXPORTED"})["event"]
    payload = {"operationId": uid(), "id": uid(), "sourceEventId": source["id"],
               "title": "Selected engineering intent", "objective": "Build the selected deliverable",
               "acceptance": "Independent actual result", "reviewBudget": 1}
    deliveries = f.sql("SELECT count(*) AS n FROM hb_delivery")["n"]
    try:
        route = "rooms/" + target_room + "/goals"
        await f.http(owner, "POST", route, payload, status=403)
        await f.http(owner, "POST", route, {**payload, "publishScopedIntent": "true"}, status=400)
        scoped = {**payload, "publishScopedIntent": True}
        result = await f.http(owner, "POST", route, scoped)
        assert await f.http(owner, "POST", route, scoped) == result
        await f.http(owner, "POST", route, {**scoped, "title": "Changed"}, status=409)
        goal = f.sql("SELECT * FROM hb_goal WHERE id=%s", (payload["id"],))
        assert str(goal["room"]) == target_room and str(goal["owner"]) == global_id
        assert str(goal["source"]) != str(source["id"])
        intent = f.sql("SELECT * FROM hb_event WHERE id=%s", (goal["source"],))
        assert str(intent["room"]) == target_room and intent["audience"] == "room"
        assert intent["kind"] == "message" and str(intent["source"]) == str(source["id"])
        assert intent["data"]["type"] == "goalOrigin" and intent["data"]["originAuthorKind"] == "agent"
        assert "PRIVATE_ORIGIN_BODY_IS_NOT_EXPORTED" not in json.dumps(intent, default=str)
        assert f.sql("SELECT count(*) AS n FROM hb_event WHERE data->>'goalId'=%s AND data->>'type'='goalOrigin'",
                     (payload["id"],))["n"] == 1
        assert f.sql("SELECT count(*) AS n FROM hb_delivery")["n"] == deliveries
        await f.http(reader["token"], "GET", "goals/" + payload["id"])
        await f.http(reader["token"], "GET", "inbox/events/" + str(source["id"]), status=403)

        # Same-room callers preserve original fingerprints, and no synthetic
        # scope event or fabricated human ancestor is required.
        local = {**payload, "id": uid(), "operationId": uid()}
        result = await f.http(owner, "POST", "rooms/" + origin_room + "/goals", local)
        assert await f.http(owner, "POST", "rooms/" + origin_room + "/goals",
                            {**local, "publishScopedIntent": False}) == result
        local_goal = f.sql("SELECT * FROM hb_goal WHERE id=%s", (local["id"],))
        assert str(local_goal["source"]) == str(source["id"])
        private = f.store.post(author["id"], origin_room, uid(),
            {"kind": "request", "audience": "direct", "recipientId": global_id,
             "body": "DIRECT_PRIVATE_ORIGIN"})["event"]
        await f.http(owner, "POST", route,
            {**scoped, "operationId": uid(), "id": uid(), "sourceEventId": private["id"]}, status=403)
        result = {"state": "passed", "groups": 4, "httpCalls": len(f.trace),
                  "nativeMetadata": "synthetic fixture", "modelsStarted": 0,
                  "checks": ["Agent-origin Goal without human ancestor",
                    "Explicit atomic scoped intent, stable receipt replay and no new delivery",
                    "Goal readable in target room without source-room access or body export",
                    "Same-room original fingerprint compatibility; direct-source privacy retained"]}
        (f.out / "qualification.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
    finally:
        await f.close()


if __name__ == "__main__":
    asyncio.run(main())
