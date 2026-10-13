import pytest
from aiohttp import ClientSession, web

from hollywood_room.rooms import catalog, join, set_purpose
from hollywood_room.server import Service, application
from hollywood_room.store import Conflict, Store


def test_identity_membership_restart_and_context_isolation(tmp_path):
    path = tmp_path / 'room.db'
    store = Store(path)
    store.add_task('leaf', 'Leaf context', None)
    first = catalog(store, 'default')
    rooms = {r['task']: r for r in first['rooms']}
    actor = rooms['lobby']['members'][0]['id']
    assert rooms['leaf']['members'][0]['id'] == actor
    join(store, rooms['leaf']['id'], 'default', actor)
    join(store, rooms['leaf']['id'], 'default', actor)
    assert store.rows('SELECT * FROM delivery') == []
    assert store.rows('SELECT * FROM binding') == []
    peer = store.rows("SELECT id FROM actor_catalog WHERE legacy_agent='maya'")[0]['id']
    join(store, rooms['lobby']['id'], 'default', peer)
    join(store, rooms['leaf']['id'], 'default', peer)
    assert len(store.rows('SELECT room FROM room_membership WHERE actor=?', (peer,))) == 2
    page = catalog(store, 'default', limit=1)
    rest = catalog(store, 'default', cursor=page['next_cursor'], limit=1)
    assert len(page['rooms']) == len(rest['rooms']) == 1
    assert page['rooms'][0]['id'] != rest['rooms'][0]['id']
    changed = set_purpose(store, rooms['leaf']['id'], 'default', 'Focused leaf work', 0)
    assert changed['revision'] == 1
    with pytest.raises(Conflict):
        set_purpose(store, rooms['leaf']['id'], 'default', 'Stale overwrite', 0)
    store.close()
    store = Store(path)
    second = catalog(store, 'default')
    assert second['authorityId'] == first['authorityId']
    assert {r['task']: r['id'] for r in second['rooms']} == {r['task']: r['id'] for r in first['rooms']}
    assert all(not r['membershipGrantsExecution'] for r in second['rooms'])
    assert store.rows('SELECT * FROM delivery') == []
    store.close()


async def test_api_auth_team_boundary_and_membership_no_wakeup(tmp_path):
    store = Store(tmp_path / 'room.db')
    store.add_team('research', 'Research', 'researcher', 'Research')
    runner = web.AppRunner(application(Service(store, 'test-token')))
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    base = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/hollywood/v2'
    try:
        async with ClientSession() as client:
            assert (await client.get(base + '/rooms')).status == 401
        async with ClientSession(headers={'Authorization': 'Bearer test-token'}) as client:
            default = await (await client.get(base + '/rooms')).json()
            research = await (await client.get(base + '/teams/research/rooms')).json()
            room = research['rooms'][0]
            actor = default['rooms'][0]['members'][0]['id']
            assert (await client.get(base + '/rooms/' + room['id'])).status == 400
            assert (await client.put(base + '/teams/research/rooms/' + room['id'] + '/members/' + actor)).status == 400
            scoped = base + '/teams/research/rooms/' + room['id']
            for _ in range(2):
                assert (await client.put(scoped + '/members/' + room['members'][0]['id'])).status == 200
            result = await client.put(scoped + '/purpose', json={'purpose': 'Research scope', 'revision': 0})
            assert result.status == 200
            assert (await client.put(scoped + '/purpose', json={'purpose': 'Stale', 'revision': 0})).status == 409
            assert store.rows('SELECT * FROM delivery') == []
            assert store.rows('SELECT * FROM binding') == []
    finally:
        await runner.cleanup()
        store.close()
