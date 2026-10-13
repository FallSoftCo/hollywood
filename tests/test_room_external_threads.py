import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from hollywood_room.external_threads import ExternalThreads
from hollywood_room.runtime import Runtime
from hollywood_room.server import Service, application
from hollywood_room.store import Conflict, Store

THREAD = '00000000-0000-0000-0000-000000000001'


@pytest.fixture
def runtime(tmp_path):
    store = Store(tmp_path / 'room.db')
    runtime = Runtime(store, binary='unused', home=tmp_path, state=tmp_path, workspace=tmp_path)
    runtime.rpc.call = AsyncMock(return_value={'thread': {'id': THREAD, 'status': {'type': 'active'}}})
    yield runtime
    runtime.store.close()


async def test_discovery_is_metadata_only_bounded_and_never_claims_writer(runtime):
    thread = {'id': THREAD, 'status': {'type': 'notLoaded'}, 'preview': 'private', 'turns': ['private']}
    runtime.rpc.call.return_value = {'data': [thread], 'nextCursor': 'next'}
    registry = ExternalThreads(runtime)
    page = await registry.list('lobby', 'maya', 'default', limit=1)
    assert page['nextCursor'] == 'next'
    assert not page['data'][0]['canAttachWriter']
    assert 'preview' not in page['data'][0] and 'turns' not in page['data'][0]
    runtime.rpc.call.assert_awaited_once_with('thread/list', {'cursor': None, 'limit': 1,
        'useStateDbOnly': True, 'sortKey': 'updated_at', 'sortDirection': 'desc'})
    with pytest.raises(ValueError):
        await registry.read('not-a-uuid', 'lobby', 'maya', 'default')


async def test_read_preserves_active_writer_and_rejects_wrong_identity(runtime):
    registry = ExternalThreads(runtime)
    result = await registry.read(THREAD, 'lobby', 'maya', 'default')
    assert result['status'] == {'type': 'active'}
    assert result['canAttachWriter'] is False
    runtime.rpc.call.assert_awaited_once_with('thread/read', {'threadId': THREAD, 'includeTurns': False})
    runtime.rpc.call.return_value = {'thread': {'id': '00000000-0000-0000-0000-000000000002'}}
    with pytest.raises(ValueError, match='identity mismatch'):
        await registry.read(THREAD, 'lobby', 'maya', 'default')


async def test_discovery_http_authentication_scope_and_validation(runtime):
    runtime.rpc.call.return_value = {'data': [], 'nextCursor': None}
    async with TestClient(TestServer(application(Service(runtime.store, 'fixture', runtime)))) as client:
        response = await client.get('/hollywood/v2/external-threads')
        assert response.status == 401
        headers = {'Authorization': 'Bearer fixture'}
        for query in ['', '?task=lobby&agent=maya&limit=101', '?task=lobby&agent=unknown']:
            response = await client.get('/hollywood/v2/external-threads' + query, headers=headers)
            assert response.status == 400
        runtime.rpc.call.assert_not_awaited()
        response = await client.get('/hollywood/v2/external-threads?task=lobby&agent=maya&limit=1', headers=headers)
        assert response.status == 200
        page = await response.json()
        assert page['data'] == [] and page['metadataOnly'] and not page['canAttachWriter']
        assert page['ownerId'] == ExternalThreads(runtime).owner_id


async def test_association_is_durable_retry_safe_and_does_not_change_execution(runtime):
    registry = ExternalThreads(runtime)
    before = runtime.store.snapshot('default')
    args = (THREAD, 'one', registry.owner_id, 'lobby', 'maya', 'default', 'associate')
    first, second = await asyncio.gather(registry.associate(*args), registry.associate(*args))
    assert first == second
    assert first['association']['mode'] == 'metadata'
    assert runtime.store.snapshot('default') == before
    assert runtime.active == {} and runtime.loaded == set()
    recreated = ExternalThreads(runtime)
    assert await recreated.associate(*args) == first
    await recreated.associate(THREAD, 'remove', registry.owner_id, 'lobby', 'maya', 'default', 'dissociate')
    assert runtime.store.rows('SELECT * FROM external_association') == []
    assert all(call.args[0] == 'thread/read' for call in runtime.rpc.call.await_args_list)
    assert runtime.store.snapshot('default') == before


async def test_association_rejects_wrong_owner_target_and_command_reuse(runtime):
    registry = ExternalThreads(runtime)
    with pytest.raises(Conflict, match='owner changed'):
        await registry.associate(THREAD, 'one', 'other', 'lobby', 'maya', 'default', 'associate')
    runtime.rpc.call.assert_not_awaited()
    await registry.associate(THREAD, 'one', registry.owner_id, 'lobby', 'maya', 'default', 'associate')
    count = runtime.rpc.call.await_count
    with pytest.raises(ValueError, match='unavailable'):
        await registry.read(THREAD, 'lobby', 'theo', 'default')
    assert runtime.rpc.call.await_count == count
    with pytest.raises(Conflict, match='different contents'):
        await registry.associate(THREAD, 'one', registry.owner_id, 'lobby', 'maya', 'default', 'dissociate')
    with pytest.raises(ValueError, match='this team'):
        await registry.read(THREAD, 'lobby', 'maya', 'wrong-team')


async def test_registered_other_agent_threads_hidden_before_metadata_read(runtime):
    with runtime.store.db:
        runtime.store.ensure_binding('lobby', 'theo')
        runtime.store.db.execute('UPDATE binding SET thread=? WHERE agent=?', (THREAD, 'theo'))
    registry = ExternalThreads(runtime)
    runtime.rpc.call.return_value = {'data': [{'id': THREAD, 'name': 'private'}], 'nextCursor': 'next'}
    page = await registry.list('lobby', 'maya', 'default')
    assert page['data'] == [] and page['nextCursor'] == 'next'
    with pytest.raises(ValueError, match='unavailable'):
        await registry.read(THREAD, 'lobby', 'maya', 'default')
    assert runtime.rpc.call.await_count == 1


async def test_association_rolls_back_if_receipt_cannot_commit(runtime):
    registry = ExternalThreads(runtime)
    runtime.store.db.execute("CREATE TRIGGER reject_external_receipt BEFORE INSERT ON external_command BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await registry.associate(THREAD, 'one', registry.owner_id, 'lobby', 'maya', 'default', 'associate')
    assert runtime.store.rows('SELECT * FROM external_association') == []


async def test_association_http_and_offline_list_survive_store_reopen(runtime, tmp_path):
    registry = ExternalThreads(runtime)
    payload = {'commandId': 'associate', 'expectedOwnerId': registry.owner_id,
               'task': 'lobby', 'agent': 'maya', 'action': 'associate'}
    async with TestClient(TestServer(application(Service(runtime.store, 'fixture', runtime)))) as client:
        url = f'/hollywood/v2/external-threads/{THREAD}/association'
        assert (await client.post(url, json=payload)).status == 401
        response = await client.post(url, json=payload, headers={'Authorization': 'Bearer fixture'})
        assert response.status == 200
        result = await response.json()
        assert result['association']['threadId'] == THREAD
    runtime.store.close()
    runtime.store = Store(tmp_path / 'room.db')
    registry = ExternalThreads(runtime)
    runtime.rpc.call = AsyncMock(side_effect=RuntimeError('owner unavailable'))
    page = registry.associations('lobby', 'maya', 'default')
    assert page['data'] == [result['association']]
    await registry.associate(THREAD, 'remove', registry.owner_id, 'lobby', 'maya', 'default', 'dissociate')
    assert registry.associations('lobby', 'maya', 'default')['data'] == []
    runtime.rpc.call.assert_not_awaited()
