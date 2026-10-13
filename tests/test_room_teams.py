import pytest
from aiohttp import ClientSession, web

from hollywood_room.actions import perform
from hollywood_room.models import Message
from hollywood_room.runtime import Runtime
from hollywood_room.server import Service, application
from hollywood_room.store import Store


@pytest.fixture
async def endpoint(tmp_path):
    store = Store(tmp_path / "room.db")
    runner = web.AppRunner(application(Service(store, "test-token")))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    async with ClientSession(headers={"Authorization": "Bearer test-token"}) as client:
        yield (
            store,
            client,
            f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v2",
        )
    await runner.cleanup()
    store.close()


async def test_team_creation_scope_custom_recipient_and_private_history(endpoint):
    _store, client, base = endpoint
    payload = {
        "id": "research",
        "name": "Research",
        "coordinator": "lead-r",
        "role": "Lead research",
    }
    for _ in range(2):
        response = await client.post(base + "/teams", json=payload)
        assert response.status == 201
        assert (await response.json())["lobby"] == "team-research"
    scoped = base + "/teams/research"
    response = await client.post(scoped + "/teammates", json={"id": "analyst", "role": "Analyze"})
    assert response.status == 201
    response = await client.post(
        scoped + "/messages",
        json={
            "commandId": "private",
            "body": "Secret",
            "targets": ["analyst"],
            "visibility": "direct",
        },
    )
    assert response.status == 202
    event = await response.json()
    assert event["task"] == "team-research"
    response = await client.post(
        scoped + "/messages", json={"commandId": "default-lead", "body": "Please coordinate"}
    )
    assert (await response.json())["data"]["targets"] == ["lead-r"]
    response = await client.get(scoped + "/history", params={"visibility": "direct:analyst"})
    assert [item["id"] for item in (await response.json())["data"]] == [event["id"]]
    for path in ("/messages", "/history", "/overview"):
        response = await client.get(base + path)
        assert "Secret" not in await response.text()
    for path in (
        f"/messages/{event['id']}",
        "/commands/private",
        "/history?visibility=direct:analyst",
        "/messages?task=team-research",
    ):
        response = await client.get(base + path)
        assert response.status == 400
    response = await client.post(
        base + "/messages",
        json={"commandId": "foreign-reply", "body": "No", "replyTo": event["id"]},
    )
    assert response.status == 400
    response = await client.post(
        scoped + "/messages",
        json={"commandId": "foreign-member", "body": "No", "targets": ["maya"]},
    )
    assert response.status == 400


async def test_owner_attention_across_teams_pages_and_durable_resolution(endpoint):
    store, client, base = endpoint
    store.add_team("other", "Other", "lead-o", "Lead")
    first = store.attention("lobby", "maya", "Default question", visibility="direct:maya")
    second = store.attention("team-other", "lead-o", "Other question", visibility="direct:lead-o")
    response = await client.get(base + "/attention?limit=1")
    page = await response.json()
    assert page["total_pending"] == 2
    assert page["next_cursor"] == first
    assert page["data"][0]["team_id"] == "default"
    response = await client.get(base + f"/attention?cursor={first}&limit=1")
    page = await response.json()
    assert page["data"][0]["id"] == second
    assert page["data"][0]["task_title"] == "Other"
    assert page["next_cursor"] is None
    payload = {"commandId": "answer-other", "body": "Proceed"}
    response = await client.post(base + f"/attention/{second}/answer", json=payload)
    assert response.status == 400
    for _ in range(2):
        response = await client.post(base + f"/teams/other/attention/{second}/answer", json=payload)
        assert response.status == 200
        assert (await response.json())["visibility"] == "direct:lead-o"
    response = await client.get(base + "/attention")
    assert (await response.json())["total_pending"] == 1


def test_roster_tasks_and_queued_delivery_survive_restart_without_ceilings(tmp_path):
    path = tmp_path / "room.db"
    store = Store(path)
    service = Service(store, "test")
    for index in range(12):
        store.add_teammate(f"member-{index}", "default", "Help")
    source = service.post(Message(commandId="many", body="Assign", targets=list(store.roster())))
    for index in range(105):
        store.message(
            f"assign-{index}",
            task=f"work-{index}",
            author="coordinator",
            body="Work",
            targets=["maya"],
            authority="tool",
            assignment={"title": "Work", "source": source["id"]},
        )
    for index in range(102):
        store.add_team(f"group-{index}", "Group", f"lead-{index}", "Lead")
    before = store.snapshot()
    store.close()
    store = Store(path)
    assert store.snapshot() == before
    assert len(store.teams()) == 103
    assert len(store.roster("default")) == 16
    assert len(store.get_event(source["id"])["data"]["targets"]) == 16
    store.close()


async def test_agent_tools_cannot_cross_team_scope(tmp_path):
    store = Store(tmp_path / "room.db")
    store.add_team("other", "Other", "lead-o", "Lead")
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    source = Service(store, "test").post(Message(commandId="human", body="Work", targets=["maya"]))
    delivery = store.rows("SELECT * FROM delivery WHERE event=?", (source["id"],))[0]
    active = {"delivery": delivery, "visibility": "room"}
    with pytest.raises(ValueError, match="belong"):
        await perform(
            runtime,
            ("lobby", "maya"),
            active,
            "hollywood_read_room",
            {"task": "team-other", "after": 0},
            "read",
        )
    with pytest.raises(ValueError, match="recipient"):
        await perform(
            runtime,
            ("lobby", "maya"),
            active,
            "hollywood_say",
            {"task": "lobby", "to": "lead-o", "body": "No", "response": "required"},
            "say",
        )
    store.close()


async def test_discovery_pages_every_team_and_member(endpoint):
    store, client, base = endpoint
    for index in range(105):
        store.add_teammate(f"worker-{index:03}", "default", "Help")
        store.add_team(f"g-{index:03}", "Group", f"chief-{index:03}", "Lead")
    for path, collection, count in (
        ("/teams", "teams", 106),
        ("/teams/default/teammates", "team", 109),
    ):
        cursor = ""
        found = []
        while True:
            response = await client.get(base + path, params={"cursor": cursor, "limit": 23})
            assert response.status == 200
            page = await response.json()
            assert page["total"] == count
            values = page[collection]
            assert len(values) <= 23
            found.extend([row["id"] for row in values] if collection == "teams" else values)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(set(found)) == count
    response = await client.get(base + "/overview")
    overview = await response.json()
    assert len(overview["team"]) == 100
    assert overview["teamTotal"] == 109
    assert overview["teamNextCursor"] is not None


async def test_explicit_new_task_is_retry_safe_and_uses_dynamic_tools(endpoint, tmp_path):
    from unittest.mock import AsyncMock

    store, client, base = endpoint
    store.add_team("new-team", "New", "new-lead", "Coordinate")
    store.add_teammate("new-member", "new-team", "Implement")
    payload = {
        "id": "fresh-work",
        "title": "Continue work",
        "commandId": "fresh",
        "body": "Continue from the earlier history",
        "targets": ["new-member"],
    }
    events = []
    for _ in range(2):
        response = await client.post(base + "/teams/new-team/tasks", json=payload)
        assert response.status == 202
        events.append(await response.json())
    assert events[0] == events[1]
    assert store.task_team("fresh-work") == "new-team"
    assert (
        store.rows("SELECT source FROM task WHERE id='fresh-work'")[0]["source"] == events[0]["id"]
    )
    assert len(store.rows("SELECT * FROM delivery WHERE task='fresh-work'")) == 1
    runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.rpc.call = AsyncMock(return_value={"thread": {"id": "new-thread"}})
    runtime.workspaces.for_agent = AsyncMock(return_value=tmp_path)
    await runtime.thread("fresh-work", "new-member")
    method, params = runtime.rpc.call.call_args.args
    assert method == "thread/start"
    assignment = next(tool for tool in params["dynamicTools"] if tool["name"] == "hollywood_assign")
    assert "enum" not in assignment["inputSchema"]["properties"]["agent"]
    assert any(tool["name"] == "hollywood_roster" for tool in params["dynamicTools"])
    assert "Role: Implement" in params["developerInstructions"]


@pytest.mark.parametrize("identity", ["you", "room", "hollywood", "system"])
def test_reserved_members_are_rejected(tmp_path, identity):
    store = Store(tmp_path / "room.db")
    try:
        with pytest.raises(ValueError, match="reserved"):
            store.add_teammate(identity, "default", "Role")
    finally:
        store.close()


async def test_legacy_coordinator_bootstraps_fresh_dynamic_task(tmp_path):
    from unittest.mock import AsyncMock

    store = Store(tmp_path / "room.db")
    try:
        store.add_teammate("new-worker", "default", "Implement")
        source = Service(store, "test").post(Message(commandId="human", body="Start new work"))
        with store.db:
            store.db.execute("UPDATE binding SET thread='legacy-thread' WHERE task='lobby'")
        runtime = Runtime(store, binary="unused", home=tmp_path, state=tmp_path, workspace=tmp_path)
        runtime.rpc.call = AsyncMock(return_value={"thread": {"id": "fresh-thread"}})
        runtime.workspaces.for_agent = AsyncMock(return_value=tmp_path)
        delivery = store.rows("SELECT * FROM delivery WHERE event=?", (source["id"],))[0]
        # coordinator is permitted by the original four-member tool schema.
        await perform(
            runtime,
            ("lobby", "coordinator"),
            {"delivery": delivery, "visibility": "room"},
            "hollywood_assign",
            {
                "agent": "coordinator",
                "task": "fresh",
                "title": "Fresh",
                "brief": "Continue this work",
            },
            "bootstrap",
        )
        await runtime.thread("fresh", "coordinator")
        method, params = runtime.rpc.call.call_args.args
        assert method == "thread/start"
        assert any(tool["name"] == "hollywood_roster" for tool in params["dynamicTools"])
        runtime.validators["hollywood_assign"].validate(
            {"agent": "new-worker", "task": "fresh", "title": "Fresh", "brief": "Implement"}
        )
        delivery = store.rows("SELECT * FROM delivery WHERE task='fresh' AND authority='tool'")[0]
        await perform(
            runtime,
            ("fresh", "coordinator"),
            {"delivery": delivery, "visibility": "room"},
            "hollywood_assign",
            {"agent": "new-worker", "task": "fresh", "title": "Fresh", "brief": "Implement"},
            "dynamic-assign",
        )
        assert (
            store.rows("SELECT thread FROM binding WHERE task='lobby'")[0]["thread"]
            == "legacy-thread"
        )
        assert store.rows("SELECT agent FROM binding WHERE task='fresh' ORDER BY agent") == [
            {"agent": "coordinator"},
            {"agent": "new-worker"},
        ]
    finally:
        store.close()
